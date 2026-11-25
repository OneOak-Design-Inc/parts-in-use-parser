import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

from parse_parts_in_use import CORE, KNOWN, InputError, main, parse_export, write_outputs


FIXTURE = Path(__file__).parent / "fixtures" / "synthetic.csv"
SCRIPT = Path(__file__).resolve().parents[1] / "parse_parts_in_use.py"


def make_csv(tmp_path, rows, headers=None, encoding="utf-8"):
    source = tmp_path / "input.csv"
    with source.open("w", encoding=encoding, newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(CORE if headers is None else headers)
        writer.writerows(rows)
    return source


def primary(mpn="0001", manufacturer="Example Co", used_in="Demo (R1)"):
    return [f"part: {mpn}, manufacturer: {manufacturer}", manufacturer,
            "00002", used_in, "", "Unknown"]


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_synthetic_fixture_preserves_source_and_both_layouts(tmp_path):
    before = FIXTURE.read_bytes()
    parsed = parse_export(FIXTURE)
    headers, records, manufacturers, usage, summary = parsed
    assert headers == KNOWN
    assert len(records) == 3
    assert records[0]["source_line_start"] == 2
    assert records[0]["source_line_end"] == 3
    assert manufacturers[0]["mpn_text"] == "00123"
    assert manufacturers[1]["origin"] == "Manufacturer Part Number 1"
    assert manufacturers[1]["mpn_text"] == "000045"
    assert manufacturers[1]["CPN"] == "00002"
    assert manufacturers[-1]["mpn_text"] == "00789, 00790 "
    assert manufacturers[-1]["status"] == "ambiguous_mpn_cell_preserved"
    assert len(manufacturers) == 4
    assert len(usage) == 4
    assert usage[1]["usage_label_raw"] == "Demo Board Beta [Demo Variant] (C3)"
    assert summary["counts"]["error_issues"] == 0
    write_outputs(tmp_path / "reports", parsed)
    cleaned = read_csv(tmp_path / "reports" / "cleaned.csv")
    for original, output in zip(records, cleaned):
        assert [output[h] for h in headers] == original["fields"]
    assert FIXTURE.read_bytes() == before
    assert summary["source"]["sha256"] == hashlib.sha256(before).hexdigest()


def test_unknown_columns_reordered_headers_and_quotes(tmp_path):
    headers = ["Extra note"] + list(reversed(CORE))
    values = dict(zip(CORE, primary(manufacturer='Example "Quoted", Inc.')))
    source = make_csv(tmp_path, [["  unchanged\ntext  "] + [values[h] for h in reversed(CORE)]], headers)
    parsed = parse_export(source)
    write_outputs(tmp_path / "out", parsed)
    assert read_csv(tmp_path / "out" / "cleaned.csv")[0]["Extra note"] == "  unchanged\ntext  "
    assert parsed[2][0]["manufacturer_text"] == 'Example "Quoted", Inc.'
    assert parsed[-1]["unexpected_columns"] == ["Extra note"]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "cp1252"])
def test_known_encodings_and_unicode(tmp_path, encoding):
    source = make_csv(tmp_path, [primary(manufacturer="Exämple µ")], encoding=encoding)
    parsed = parse_export(source, encoding)
    assert parsed[2][0]["manufacturer_text"] == "Exämple µ"


def test_invalid_encoding_is_fatal(tmp_path):
    source = tmp_path / "input.csv"
    source.write_bytes(b"\xff\xfe\x80")
    with pytest.raises(InputError, match="decode"):
        parse_export(source)


@pytest.mark.parametrize("headers", [[], [""], CORE + [CORE[0]], CORE[:-1], CORE + ["__piu_test"]])
def test_unsafe_headers_are_rejected(tmp_path, headers):
    source = make_csv(tmp_path, [], headers)
    with pytest.raises(InputError):
        parse_export(source)


def test_empty_file(tmp_path):
    source = tmp_path / "empty.csv"
    source.write_text("")
    with pytest.raises(InputError, match="empty"):
        parse_export(source)


def test_short_long_and_blank_records_are_retained(tmp_path):
    source = make_csv(tmp_path, [primary()[:-2], primary() + ["extra", "0000"], []])
    parsed = parse_export(source)
    assert len(parsed[1]) == 3
    assert parsed[-1]["counts"]["error_issues"] == 3
    assert parsed[-1]["absent_cells_by_column"]["Lifecycle"] == 2
    assert parsed[-1]["empty_cells_by_column"]["Description"] == 1
    write_outputs(tmp_path / "out", parsed)
    cleaned = read_csv(tmp_path / "out" / "cleaned.csv")
    assert [r["__piu_field_count"] for r in cleaned] == ["4", "8", "0"]
    assert json.loads(cleaned[1]["__piu_extra_fields_json"]) == ["extra", "0000"]
    assert main([str(source), "--output-dir", str(tmp_path / "cli")]) == 2


def test_unclosed_quote_fails_before_outputs(tmp_path, capsys):
    source = make_csv(tmp_path, [primary()])
    with source.open("a", encoding="utf-8") as stream:
        stream.write('"unclosed\nfield')
    output = tmp_path / "out"
    assert main([str(source), "--output-dir", str(output)]) == 1
    assert not output.exists()
    assert "record beginning at physical line 3" in capsys.readouterr().err


def test_duplicate_rows_stay_separate(tmp_path):
    source = make_csv(tmp_path, [primary(), primary(), primary()])
    parsed = parse_export(source)
    assert len(parsed[1]) == len(parsed[2]) == len(parsed[3]) == 3
    assert parsed[-1]["exact_duplicate_groups"] == [[1, 2, 3]]
    assert parsed[-1]["counts"]["exact_duplicate_excess_rows"] == 2


@pytest.mark.parametrize("used_in", ["", "  "])
def test_no_usage_text_is_not_zero_usage(tmp_path, used_in):
    parsed = parse_export(make_csv(tmp_path, [primary(used_in=used_in)]))
    assert parsed[3] == []
    assert parsed[-1]["counts"]["rows_without_usage_text"] == 1
    assert "unknown usage" in parsed[-1]["interpretation"]["usage"]


def test_usage_separators_blank_lines_and_large_cell(tmp_path):
    text = "First (R1)\r\n\r\nSecond (R2)\r" + "X" * 140000
    parsed = parse_export(make_csv(tmp_path, [primary(used_in=text)]))
    assert [r["usage_label_raw"] for r in parsed[3]] == ["First (R1)", "", "Second (R2)", "X" * 140000]
    assert all(r["used_in_raw"] == text for r in parsed[3])
    assert parsed[1][0]["source_line_end"] == 5


@pytest.mark.parametrize("composite", ["0001", "part: 1, manufacturer: X, manufacturer: Y"])
def test_unknown_primary_format_is_preserved(tmp_path, composite):
    row = primary()
    row[0] = composite
    parsed = parse_export(make_csv(tmp_path, [row]))
    assert parsed[2][0]["mpn_cell_raw"] == composite
    assert parsed[2][0]["mpn_text"] == ""
    assert parsed[-1]["counts"]["error_issues"] == 1


def test_manufacturer_conflict_is_explicit(tmp_path):
    row = primary()
    row[1] = "Other Example"
    parsed = parse_export(make_csv(tmp_path, [row]))
    assert parsed[2][0]["status"] == "manufacturer_conflict"
    assert parsed[2][0]["manufacturer_cell_raw"] == "Other Example"
    assert parsed[2][0]["manufacturer_text"] == "Example Co"


@pytest.mark.parametrize("manufacturer,mpn", [("Example", ""), ("", "00001")])
def test_incomplete_numbered_pair(tmp_path, manufacturer, mpn):
    source = make_csv(tmp_path, [primary() + [manufacturer, mpn]], CORE + KNOWN[6:8])
    parsed = parse_export(source)
    assert parsed[2][-1]["status"] == "incomplete_pair"
    assert parsed[-1]["counts"]["error_issues"] == 1


def test_no_output_overwrite_including_source(tmp_path):
    source = make_csv(tmp_path, [primary()])
    parsed = parse_export(source)
    out = tmp_path / "out"
    write_outputs(out, parsed)
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(InputError, match="already exist"):
        write_outputs(out, parsed)
    assert before == {p.name: p.read_bytes() for p in out.iterdir()}
    # A source named like an output can never be overwritten either.
    renamed_source = tmp_path / "cleaned.csv"
    renamed_source.write_bytes(source.read_bytes())
    with pytest.raises(InputError):
        write_outputs(tmp_path, parse_export(renamed_source))
    assert renamed_source.read_bytes() == source.read_bytes()


def test_cli_standard_library_execution(tmp_path):
    result = subprocess.run([sys.executable, "-I", "-S", str(SCRIPT), str(FIXTURE),
                             "--output-dir", str(tmp_path / "out")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "3 source records" in result.stdout
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["usage_label_lines"] == 4


def test_header_only_export(tmp_path):
    parsed = parse_export(make_csv(tmp_path, []))
    assert parsed[-1]["counts"]["source_records"] == 0
    write_outputs(tmp_path / "out", parsed)
    assert read_csv(tmp_path / "out" / "usage.csv") == []
