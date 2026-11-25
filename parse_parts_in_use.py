"""Offline parser for the observed Altium Parts in Use CSV. Python 3.11+."""

import argparse
import copy
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sys

from excel_report import ReportError, build_report, workbook_bytes


CORE = ["Manufacturer Part Number", "Manufacturer", "CPN", "Used In",
        "Description", "Lifecycle"]
REQUIRED_HEADERS = [name for name in CORE if name != "Manufacturer"]
PAIRS = [(f"Manufacturer {i}", f"Manufacturer Part Number {i}")
         for i in range(1, 5)]
KNOWN = CORE + [name for pair in PAIRS for name in pair]
PREFIX = "__piu_"
OUTPUTS = ("cleaned.csv", "manufacturer_fields.csv", "usage.csv", "summary.json")


class InputError(ValueError):
    """Input cannot be parsed without guessing boundaries or column meanings."""


def decode_backslash_quotes(text):
    """Translate only backslash-quote pairs inside CSV quoted cells.

    Using csv's general escapechar would also consume literal backslashes in
    paths, identifiers and backslash-n strings. Leave those characters intact.
    Standard doubled quotes remain supported, and line boundaries never change.
    """
    chunks = []
    index = copied_until = replacements = 0
    in_quotes = False
    field_start = True
    while index < len(text):
        char = text[index]
        if in_quotes:
            if text.startswith('\\"', index):
                chunks.extend((text[copied_until:index], '""'))
                replacements += 1
                index += 2
                copied_until = index
                continue
            if char == '"':
                if text.startswith('""', index):
                    index += 2
                    continue
                in_quotes = False
        elif char == '"' and field_start:
            in_quotes = True
            field_start = False
        else:
            field_start = char in ",\r\n"
        index += 1
    chunks.append(text[copied_until:])
    return "".join(chunks), replacements


def parse_export(source, encoding="utf-8-sig", backslash_escaped_quotes=False):
    """Read once, preserve strings, and return all output data before writing."""
    source = Path(source)
    raw = source.read_bytes()
    try:
        text = raw.decode(encoding, errors="strict")
    except (UnicodeError, LookupError) as exc:
        raise InputError("Cannot decode input; specify its known --encoding. "
                         "No replacement characters were inserted.") from exc
    # csv's default 128 KiB limit is too small for long usage lists.
    csv.field_size_limit(2**31 - 1)
    csv_text, quote_replacements = (decode_backslash_quotes(text)
                                    if backslash_escaped_quotes else (text, 0))
    reader = csv.reader(io.StringIO(csv_text, newline=""), delimiter=",",
                        quotechar='"', doublequote=True, strict=True)
    try:
        headers = next(reader)
    except StopIteration as exc:
        raise InputError("Input is empty.") from exc
    except csv.Error as exc:
        raise InputError(f"Invalid CSV header at physical line {reader.line_num}. "
                         f"CSV reader: {exc}. No outputs written.") from exc
    # The owner confirmed an export with an entirely empty first column.
    # Keep its blank header and cells; never assign it an invented meaning.
    empty_leading_column = bool(headers) and headers[0] == ""
    named_headers = headers[1:] if empty_leading_column else headers
    if not headers or any(not h.strip() for h in named_headers):
        raise InputError("Blank column header; cannot assign values safely.")
    if len(set(headers)) != len(headers):
        raise InputError("Duplicate column headers; cannot assign values safely.")
    if any(h.startswith(PREFIX) for h in headers):
        raise InputError(f"Source header uses reserved prefix {PREFIX!r}.")
    missing_headers = [h for h in REQUIRED_HEADERS if h not in headers]
    if missing_headers:
        raise InputError("Missing observed headers: " + ", ".join(missing_headers)
                         + ". Expected comma-delimited CSV with exact header names.")

    issues = []

    def issue(code, severity="warning", record=None, **details):
        entry = {"code": code, "severity": severity, **details}
        if record is not None:
            entry.update(source_record=record["source_record"],
                         source_line_start=record["source_line_start"],
                         source_line_end=record["source_line_end"])
        issues.append(entry)

    if empty_leading_column:
        issue("empty_leading_column_preserved", severity="info", source_column_number=1)
    unexpected = [h for h in named_headers if h not in KNOWN]
    if unexpected:
        issue("unexpected_columns_preserved", columns=unexpected)
    absent_optional = [h for h in ["Manufacturer"] + [name for pair in PAIRS for name in pair]
                       if h not in headers]
    if absent_optional:
        issue("optional_columns_absent", severity="info", columns=absent_optional)

    records, manufacturer_fields, usage = [], [], []
    empty = Counter({h: 0 for h in headers})
    absent = Counter({h: 0 for h in headers})
    whitespace = Counter({h: 0 for h in headers})
    seen = {}
    duplicate_groups = {}
    while True:
        start = reader.line_num + 1
        try:
            fields = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise InputError(f"Invalid CSV in record beginning at physical line {start}; "
                             f"reader stopped at line {reader.line_num}. "
                             f"CSV reader: {exc}. "
                             "Cannot safely recover record boundaries; no outputs written.") from exc
        if empty_leading_column and fields and fields[0] != "":
            raise InputError(f"Unnamed first column contains data in the record beginning "
                             f"at physical line {start}. Only an entirely empty unnamed "
                             "first column is supported; no outputs written.")
        record = {"source_record": len(records) + 1, "source_line_start": start,
                  "source_line_end": reader.line_num, "fields": fields}
        records.append(record)
        if len(fields) != len(headers):
            issue("field_count_mismatch", "error", record,
                  expected=len(headers), actual=len(fields))
        values = dict(zip(headers, fields))
        for h in headers:
            if h not in values:
                absent[h] += 1
            elif values[h] == "":
                empty[h] += 1
            elif not values[h].strip():
                whitespace[h] += 1
        key = tuple(fields)
        if key in seen:
            first = seen[key]
            duplicate_groups.setdefault(first, [first]).append(record["source_record"])
            issue("exact_duplicate_preserved", "info", record, first_source_record=first)
        else:
            seen[key] = record["source_record"]
        provenance = {k: record[k] for k in
                      ("source_record", "source_line_start", "source_line_end")}

        def add_manufacturer(origin, manufacturer_raw, mpn_raw, manufacturer, mpn, status):
            manufacturer_fields.append({**provenance, "CPN": values.get("CPN", ""),
                "origin": origin, "manufacturer_cell_raw": manufacturer_raw,
                "mpn_cell_raw": mpn_raw, "manufacturer_text": manufacturer,
                "mpn_text": mpn, "status": status})

        composite = values.get("Manufacturer Part Number", "")
        dedicated_manufacturer = values.get("Manufacturer", "")
        if composite.strip():
            # Only the exact wrapper observed in the supplied file is interpreted.
            match = re.fullmatch(r"part: (.+), manufacturer: (.+)", composite)
            if match and composite.count(", manufacturer: ") == 1:
                mpn, manufacturer = match.groups()
                status = "composite_wrapper_extracted"
                if dedicated_manufacturer and dedicated_manufacturer != manufacturer:
                    status = "manufacturer_conflict"
                    issue(status, "error", record)
                add_manufacturer("primary", dedicated_manufacturer, composite,
                                 manufacturer, mpn, status)
            else:
                issue("unrecognized_primary_mpn_format", "error", record)
                add_manufacturer("primary", dedicated_manufacturer, composite,
                                 dedicated_manufacturer, "", "unparsed_raw_preserved")
        elif dedicated_manufacturer.strip():
            issue("primary_mpn_missing", "error", record)
            add_manufacturer("primary", dedicated_manufacturer, composite,
                             dedicated_manufacturer, "", "incomplete_pair")

        for manufacturer_column, mpn_column in PAIRS:
            manufacturer = values.get(manufacturer_column, "")
            mpn = values.get(mpn_column, "")
            if not manufacturer.strip() and not mpn.strip():
                continue
            status = "cell_preserved"
            if not manufacturer.strip() or not mpn.strip():
                status = "incomplete_pair"
                issue(status, "error", record, columns=[manufacturer_column, mpn_column])
            elif "," in mpn or "\n" in mpn or "\r" in mpn:
                status = "ambiguous_mpn_cell_preserved"
                issue(status, "warning", record, column=mpn_column)
            add_manufacturer(mpn_column, manufacturer, mpn, manufacturer, mpn, status)

        used_in = values.get("Used In", "")
        if used_in.strip():
            for index, label in enumerate(re.split(r"\r\n|\r|\n", used_in), 1):
                usage.append({**provenance, "CPN": values.get("CPN", ""),
                              "usage_line_number": index, "usage_label_raw": label,
                              "used_in_raw": used_in})
                if not label.strip():
                    issue("blank_usage_line_preserved", "warning", record,
                          usage_line_number=index)

    summary = {
        "source": {"path": str(source.resolve()), "sha256": hashlib.sha256(raw).hexdigest(),
                   "bytes": len(raw), "encoding_used": encoding,
                   "utf8_bom": raw.startswith(b"\xef\xbb\xbf"),
                   "delimiter": ",", "quotechar": '"', "headers": headers,
                   "backslash_escaped_quotes": backslash_escaped_quotes,
                   "backslash_quote_pairs_decoded": quote_replacements,
                   "crlf_count": text.count("\r\n"),
                   "bare_lf_count": text.count("\n") - text.count("\r\n"),
                   "bare_cr_count": text.count("\r") - text.count("\r\n")},
        "counts": {"source_records": len(records),
                   "manufacturer_field_records": len(manufacturer_fields),
                   "usage_label_lines": len(usage),
                   "rows_without_usage_text": sum(
                       not dict(zip(headers, r["fields"])).get("Used In", "").strip()
                       for r in records),
                   "exact_duplicate_excess_rows": sum(len(v)-1 for v in duplicate_groups.values()),
                   "error_issues": sum(i["severity"] == "error" for i in issues),
                   "warning_issues": sum(i["severity"] == "warning" for i in issues)},
        "empty_cells_by_column": dict(empty), "absent_cells_by_column": dict(absent),
        "whitespace_only_cells_by_column": dict(whitespace),
        "unexpected_columns": unexpected, "absent_optional_columns": absent_optional,
        "exact_duplicate_groups": list(duplicate_groups.values()), "issues": issues,
        "interpretation": {
            "normalization": "None: no case folding, trimming, MPN splitting, or identity matching. "
                             "Only exact primary wrapper extraction and usage newline separation.",
            "usage": "Opaque display label lines; not project/release/variant IDs or quantities. "
                     "No usage text means unknown usage, never confirmed zero usage.",
            "manufacturer_fields": "Exported fields, not verified complete Part Choices or "
                                   "managed component identities. Rows are not deduplicated.",
            "scope": "No evidence of unused components, historical revisions, library "
                     "completeness, fitted quantities, or manufacturing demand."}}
    return headers, records, manufacturer_fields, usage, summary


def write_outputs(output_dir, parsed, excel=False):
    headers, records, manufacturer_fields, usage, summary = parsed
    summary = copy.deepcopy(summary)
    output_dir = Path(output_dir)
    paths = [output_dir / name for name in OUTPUTS]
    if excel:
        paths.append(output_dir / "parts_in_use.xlsx")
    if any(p.exists() or p.is_symlink() for p in paths):
        raise InputError("Output files already exist. Choose a fresh output directory; "
                         "existing files are never overwritten.")
    if excel:
        report = build_report(parsed)
        xlsx = workbook_bytes(report)  # Validate Excel limits before writing any outputs.
        summary["issues"].extend(report["issues"])
        for severity in ("error", "warning"):
            summary["counts"][f"{severity}_issues"] = sum(
                issue["severity"] == severity for issue in summary["issues"])
        summary["excel"] = {
            "file": "parts_in_use.xlsx", "board_count": len(report["boards"]),
            "variant_columns": len(report["columns"]), "part_rows": len(report["matrix"]),
            "usage_label_rule": "Board names contain no spaces. Split at the first space: "
                                "the first word is the board and all remaining label text "
                                "before the reference list is the variant.",
            "manufacturer_mapping": "Primary wrapper -> pair 1; export numbered pairs 1-3 -> "
                                    "pairs 2-4. Export pair 4 retained in Source data.",
            "counts": "Comma-separated reference tokens per source row and exact board/variant "
                      "label. Repeated or invalid entries are REVIEW. Missing entries are blank, "
                      "not confirmed zero usage. These counts are not fitted quantities or demand."}
    output_dir.mkdir(parents=True, exist_ok=True)

    def write_csv(path, columns, rows):
        with path.open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerows(rows)

    metadata = [PREFIX + name for name in
                ("source_record", "source_line_start", "source_line_end",
                 "field_count", "extra_fields_json")]
    write_csv(paths[0], metadata + headers,
              ([r["source_record"], r["source_line_start"], r["source_line_end"],
                len(r["fields"]), json.dumps(r["fields"][len(headers):], ensure_ascii=False)]
               + r["fields"][:len(headers)]
               + [""] * max(0, len(headers) - len(r["fields"])) for r in records))
    manufacturer_columns = ["source_record", "source_line_start", "source_line_end", "CPN",
                            "origin", "manufacturer_cell_raw", "mpn_cell_raw",
                            "manufacturer_text", "mpn_text", "status"]
    usage_columns = ["source_record", "source_line_start", "source_line_end", "CPN",
                     "usage_line_number", "usage_label_raw", "used_in_raw"]
    write_csv(paths[1], manufacturer_columns,
              ([r[c] for c in manufacturer_columns] for r in manufacturer_fields))
    write_csv(paths[2], usage_columns, ([r[c] for c in usage_columns] for r in usage))
    if excel:
        with paths[4].open("xb") as stream:
            stream.write(xlsx)
    with paths[3].open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(summary, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Observed Parts in Use CSV (read only)")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Directory for new output files; existing files are refused")
    parser.add_argument("--excel", action="store_true",
                        help="Also write an Excel matrix with grouped boards, variants and refdes counts")
    parser.add_argument("--encoding", default="utf-8-sig",
                        help="Known source encoding (default: utf-8-sig, strict decoding)")
    parser.add_argument("--backslash-escaped-quotes", action="store_true",
                        help=r'Decode \" inside quoted cells; preserve other backslashes '
                             '(use for the observed BOM Portal quote style)')
    args = parser.parse_args(argv)
    try:
        parsed = parse_export(args.source, args.encoding, args.backslash_escaped_quotes)
        summary = write_outputs(args.output_dir, parsed, args.excel)
    except (OSError, InputError, ReportError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    counts = summary["counts"]
    print(f"Read {counts['source_records']} source records; wrote "
          f"{counts['manufacturer_field_records']} manufacturer field records and "
          f"{counts['usage_label_lines']} usage label lines. "
          f"Issues: {counts['error_issues']} errors, {counts['warning_issues']} warnings. "
          "Review summary.json. Usage line counts are not quantities.")
    if args.excel:
        print("Wrote parts_in_use.xlsx. Excel counts are listed reference designators, "
              "not fitted quantities or manufacturing demand.")
    return 2 if counts["error_issues"] else 0


if __name__ == "__main__":
    sys.exit(main())
