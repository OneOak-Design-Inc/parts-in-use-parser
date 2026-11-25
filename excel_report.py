"""Offline Excel usage matrix. Only Python's standard library is required."""

from collections import defaultdict
import io
import json
import math
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile


PART_HEADERS = ["CPN", "Description", "Manufacturer 1", "Lifecycle", "MPN 1",
                "Manufacturer 2", "MPN 2", "Manufacturer 3", "MPN 3",
                "Manufacturer 4", "MPN 4"]
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ET.register_namespace("", NS)
ET.register_namespace("r", REL)


class ReportError(ValueError):
    """The workbook cannot be represented without losing source data."""


def split_usage_label(raw):
    """Observed rule: final token or bracketed suffix is the variant."""
    match = re.fullmatch(r"(.+) (\[[^\[\]]+\]|[^\s\[\]]+) \(([^()]*)\)", raw)
    if not match:
        return None
    board, variant, ref_text = match.groups()
    if not board.strip():
        return None
    refs = [ref.strip() for ref in ref_text.split(",")]
    # No range expansion, DNP inference, deduplication or whitespace guessing.
    valid = all(ref and not re.search(r"\s", ref) for ref in refs)
    if not valid:
        status = "invalid_reference_list"
    elif len(set(refs)) != len(refs):
        status = "duplicate_reference_designators"
    else:
        status = "counted"
    return board, variant, ref_text, len(refs) if status == "counted" else None, status


def build_report(parsed):
    """Build a matrix without joining rows by CPN or summing variants."""
    headers, records, manufacturers, usage, summary = parsed
    issues, details = [], []
    boards = {}
    grouped = defaultdict(list)
    uncertain_rows = set()
    valid_rows = {r["source_record"] for r in records if len(r["fields"]) == len(headers)}

    def issue(code, row, severity="error", **extra):
        issues.append({"code": code, "severity": severity,
                       "source_record": row["source_record"],
                       "source_line_start": row["source_line_start"],
                       "source_line_end": row["source_line_end"], **extra})

    for row in usage:
        record = row["source_record"]
        result = split_usage_label(row["usage_label_raw"]) if record in valid_rows else None
        detail = [record, row["source_line_start"], row["source_line_end"],
                  row["CPN"], row["usage_line_number"], row["usage_label_raw"]]
        if result is None:
            uncertain_rows.add(record)
            code = "usage_label_unrecognized" if record in valid_rows else "source_row_width"
            issue(code, row, usage_line_number=row["usage_line_number"])
            details.append(detail + ["", "", "", None, code])
            continue
        board, variant, ref_text, count, status = result
        variants = boards.setdefault(board, [])
        if variant not in variants:
            variants.append(variant)
        grouped[(record, board, variant)].append((count, len(details)))
        details.append(detail + [board, variant, ref_text, count, status])
        if status != "counted":
            issue(status, row, usage_line_number=row["usage_line_number"])

    for (record, board, variant), entries in grouped.items():
        if len(entries) > 1:
            source = records[record - 1]
            issue("repeated_board_variant_label", source, board=board, variant=variant)
            for _, index in entries:
                details[index][-1] = "repeated_board_variant_label"

    columns = [(board, variant) for board, variants in boards.items() for variant in variants]
    primary = {r["source_record"]: r for r in manufacturers if r["origin"] == "primary"}
    matrix = []
    for record in records:
        number = record["source_record"]
        values = dict(zip(headers, record["fields"]))
        part = primary.get(number, {})
        extracted = part.get("status") in ("composite_wrapper_extracted", "manufacturer_conflict")
        row = [values.get("CPN", ""), values.get("Description", ""),
               part.get("manufacturer_text", "") if extracted else "",
               values.get("Lifecycle", ""), part.get("mpn_text", "") if extracted else ""]
        for pair in range(1, 4):
            row.extend([values.get(f"Manufacturer {pair}", ""),
                        values.get(f"Manufacturer Part Number {pair}", "")])
        if values.get("Manufacturer 4", "").strip() or values.get("Manufacturer Part Number 4", "").strip():
            issue("manufacturer_pair_overflow_in_source_data", record, severity="warning")
        for board, variant in columns:
            entries = grouped.get((number, board, variant), [])
            if len(entries) == 1 and entries[0][0] is not None:
                row.append(entries[0][0])
            elif entries or number in uncertain_rows or number not in valid_rows:
                row.append("REVIEW")
            else:
                row.append(None)  # Absence of a label is not confirmed zero usage.
        matrix.append(row)
    return {"boards": boards, "columns": columns, "matrix": matrix,
            "details": details, "issues": issues,
            "source": summary["source"], "records": records, "headers": headers}


def column_name(number):
    result = ""
    while number:
        number, rem = divmod(number - 1, 26)
        result = chr(65 + rem) + result
    return result


def element(parent, tag, attrs=None, text=None):
    child = ET.SubElement(parent, f"{{{NS}}}{tag}", attrs or {})
    child.text = text
    return child


def styles_xml():
    root = ET.Element(f"{{{NS}}}styleSheet")
    fonts = element(root, "fonts", {"count": "3"})
    for bold, color in [(False, "172B4D"), (True, "FFFFFF"), (True, "9C2F19")]:
        font = element(fonts, "font")
        element(font, "sz", {"val": "10"})
        element(font, "name", {"val": "Arial"})
        element(font, "color", {"rgb": "FF" + color})
        if bold:
            element(font, "b")
    fills = element(root, "fills", {"count": "6"})
    for pattern, color in [("none", None), ("gray125", None),
                           ("solid", "263B56"), ("solid", "486484"),
                           ("solid", "F1F5F9"), ("solid", "FFF1E6")]:
        fill = element(fills, "fill")
        pattern_fill = element(fill, "patternFill", {"patternType": pattern})
        if color:
            element(pattern_fill, "fgColor", {"rgb": "FF" + color})
            element(pattern_fill, "bgColor", {"indexed": "64"})
    borders = element(root, "borders", {"count": "1"})
    border = element(borders, "border")
    for edge in ("left", "right", "top", "bottom", "diagonal"):
        element(border, edge)
    base = element(root, "cellStyleXfs", {"count": "1"})
    element(base, "xf", {"numFmtId": "0", "fontId": "0", "fillId": "0", "borderId": "0"})
    xfs = element(root, "cellXfs", {"count": "7"})
    # Default text, two header bands, text body, alternate body, integer, review.
    for font, fill, num_format, align in [(0, 0, 49, "left"), (1, 2, 49, "center"),
                                         (1, 3, 49, "center"), (0, 0, 49, "left"),
                                         (0, 4, 49, "left"), (0, 0, 3, "right"),
                                         (2, 5, 49, "center")]:
        xf = element(xfs, "xf", {"fontId": str(font), "fillId": str(fill),
                               "borderId": "0", "numFmtId": str(num_format),
                               "xfId": "0", "applyAlignment": "1", "applyNumberFormat": "1"})
        element(xf, "alignment", {"horizontal": align, "vertical": "center", "wrapText": "1"})
    styles = element(root, "cellStyles", {"count": "1"})
    element(styles, "cellStyle", {"name": "Normal", "xfId": "0", "builtinId": "0"})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def worksheet_xml(rows, widths, header_rows=1, merges=(), freeze_columns=0):
    if len(rows) > 1048576 or max((len(r) for r in rows), default=0) > 16384:
        raise ReportError("Report exceeds Excel's worksheet row/column limits.")
    root = ET.Element(f"{{{NS}}}worksheet")
    last_col = column_name(len(widths))
    element(root, "dimension", {"ref": f"A1:{last_col}{len(rows)}"})
    views = element(root, "sheetViews")
    view = element(views, "sheetView", {"workbookViewId": "0", "showGridLines": "0"})
    cell = f"{column_name(freeze_columns + 1)}{header_rows + 1}"
    pane = {"ySplit": str(header_rows), "topLeftCell": cell,
            "activePane": "bottomRight" if freeze_columns else "bottomLeft", "state": "frozen"}
    if freeze_columns:
        pane["xSplit"] = str(freeze_columns)
    element(view, "pane", pane)
    element(root, "sheetFormatPr", {"defaultRowHeight": "22"})
    cols = element(root, "cols")
    for index, width in enumerate(widths, 1):
        element(cols, "col", {"min": str(index), "max": str(index),
                             "width": str(width), "customWidth": "1"})
    data = element(root, "sheetData")
    for row_number, values in enumerate(rows, 1):
        height = 34 if row_number <= header_rows else 24
        if row_number > header_rows:
            lines = max((sum(max(1, math.ceil(len(line) / max(1, widths[i] - 2)))
                             for line in str(value).split("\n"))
                         for i, value in enumerate(values) if value is not None), default=1)
            height = min(409, max(24, lines * 13 + 8))
        row = element(data, "row", {"r": str(row_number), "ht": str(height), "customHeight": "1"})
        for index, value in enumerate(values, 1):
            if value is None:
                continue
            address = f"{column_name(index)}{row_number}"
            style = (1 if row_number == 1 else 2) if row_number <= header_rows else (
                6 if value == "REVIEW" else 5 if isinstance(value, int) else 4 if row_number % 2 else 3)
            cell = element(row, "c", {"r": address, "s": str(style),
                                      "t": "n" if isinstance(value, int) else "inlineStr"})
            if isinstance(value, int):
                element(cell, "v", text=str(value))
            else:
                value = str(value)
                if len(value.encode("utf-16-le")) // 2 > 32767:
                    raise ReportError(f"Cell {address} exceeds Excel's text limit; no values were truncated.")
                if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]", value):
                    raise ReportError(f"Cell {address} contains XML-incompatible characters; source preserved.")
                # inlineStr keeps leading zeroes and leading '=' as literal text.
                inline = element(cell, "is")
                element(inline, "t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"}, value)
    if len(rows) > header_rows:
        element(root, "autoFilter", {"ref": f"A{header_rows}:{last_col}{len(rows)}"})
    if merges:
        merge_cells = element(root, "mergeCells", {"count": str(len(merges))})
        for ref in merges:
            element(merge_cells, "mergeCell", {"ref": ref})
    element(root, "pageMargins", {"left": "0.3", "right": "0.3", "top": "0.5", "bottom": "0.5",
                                  "header": "0.2", "footer": "0.2"})
    # XML parsers otherwise normalize literal CR/CRLF within cell text to LF.
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).replace(b"\r", b"&#13;")


def workbook_bytes(report):
    """Write a small OOXML workbook locally; no Excel/package installation."""
    board_header, variant_header = PART_HEADERS[:], [None] * len(PART_HEADERS)
    merges = [f"{column_name(i)}1:{column_name(i)}2" for i in range(1, 12)]
    for board, variants in report["boards"].items():
        start = len(board_header) + 1
        board_header.extend([board] + [None] * (len(variants) - 1))
        variant_header.extend(variants)
        if len(variants) > 1:
            merges.append(f"{column_name(start)}1:{column_name(len(board_header))}1")
    widths = [16, 52, 24, 16, 26, 24, 26, 24, 26, 24, 26]
    widths += [max(18, min(42, len(variant) + 2)) for _, variant in report["columns"]]
    sheets = [("Parts in Use", worksheet_xml([board_header, variant_header] + report["matrix"],
                                             widths, 2, merges, 2))]
    detail_headers = ["Source record", "Source line start", "Source line end", "CPN", "Usage line",
                      "Used In line (original)", "Board", "Variant", "Reference list (original)",
                      "Listed refdes count", "Status"]
    sheets.append(("Usage details", worksheet_xml([detail_headers] + report["details"],
                                                  [16, 16, 16, 16, 14, 65, 30, 24, 65, 20, 36], 1, (), 0)))
    source_headers = ["Report row", "Source record", "Source line start", "Source line end",
                      "Original field count"] + report["headers"] + ["Extra fields JSON"]
    source_rows = [[index + 3, r["source_record"], r["source_line_start"], r["source_line_end"],
                    len(r["fields"])] + r["fields"][:len(report["headers"])]
                   + [""] * max(0, len(report["headers"]) - len(r["fields"]))
                   + [json.dumps(r["fields"][len(report["headers"]):], ensure_ascii=False)]
                   for index, r in enumerate(report["records"])]
    source_note = (f"Source: {Path(report['source']['path']).name}; SHA-256: {report['source']['sha256']}. "
                   "Counts are listed refdes, not fitted quantity or demand.")
    meaning_note = "Blank = no usable exported entry, not zero usage. REVIEW = ambiguous/invalid entry; see summary.json."
    source_widths = [16] * 5 + [65 if h in ("Description", "Used In") else 28 for h in report["headers"]] + [32]
    sheets.append(("Source data", worksheet_xml([[source_note], [meaning_note], source_headers] + source_rows,
                                                source_widths, 3, ("A1:K1", "A2:K2"), 0)))
    content_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    contents = ET.Element("Types", xmlns=content_ns)
    for extension, content in [("rels", "application/vnd.openxmlformats-package.relationships+xml"),
                               ("xml", "application/xml")]:
        ET.SubElement(contents, "Default", Extension=extension, ContentType=content)
    types = [("/xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"),
             ("/xl/styles.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml")]
    types += [(f"/xl/worksheets/sheet{i}.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
              for i in range(1, len(sheets) + 1)]
    for path, content in types:
        ET.SubElement(contents, "Override", PartName=path, ContentType=content)
    root_rels = ET.Element("Relationships", xmlns=rel_ns)
    ET.SubElement(root_rels, "Relationship", Id="rId1", Type=REL + "/officeDocument", Target="xl/workbook.xml")
    workbook = ET.Element(f"{{{NS}}}workbook")
    sheet_list = element(workbook, "sheets")
    workbook_rels = ET.Element("Relationships", xmlns=rel_ns)
    for index, (name, _) in enumerate(sheets, 1):
        element(sheet_list, "sheet", {"name": name, "sheetId": str(index), f"{{{REL}}}id": f"rId{index}"})
        ET.SubElement(workbook_rels, "Relationship", Id=f"rId{index}", Type=REL + "/worksheet",
                      Target=f"worksheets/sheet{index}.xml")
    ET.SubElement(workbook_rels, "Relationship", Id="rId4", Type=REL + "/styles", Target="styles.xml")
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path, node in [("[Content_Types].xml", contents), ("_rels/.rels", root_rels),
                           ("xl/workbook.xml", workbook), ("xl/_rels/workbook.xml.rels", workbook_rels)]:
            archive.writestr(path, ET.tostring(node, encoding="utf-8", xml_declaration=True))
        archive.writestr("xl/styles.xml", styles_xml())
        for index, (_, content) in enumerate(sheets, 1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", content)
    return output.getvalue()
