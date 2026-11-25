import csv
import io
import json
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from excel_report import NS, PART_HEADERS, ReportError, build_report, split_usage_label, workbook_bytes
from parse_parts_in_use import KNOWN, InputError, main, parse_export, write_outputs


def source_csv(tmp_path, rows):
    source = tmp_path / "synthetic.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(KNOWN)
        for overrides in rows:
            values = {"Manufacturer Part Number": "part: 00001, manufacturer: Example Co",
                      "Manufacturer": "Example Co", "CPN": "00002",
                      "Description": "Synthetic component", "Lifecycle": "Production"}
            values.update(overrides)
            writer.writerow([values.get(h, "") for h in KNOWN])
    return source


def cells(content, sheet=1):
    with ZipFile(io.BytesIO(content)) as archive:
        root = ET.fromstring(archive.read(f"xl/worksheets/sheet{sheet}.xml"))
    values = {}
    for cell in root.findall(f".//{{{NS}}}c"):
        text = cell.find(f"{{{NS}}}is/{{{NS}}}t")
        number = cell.find(f"{{{NS}}}v")
        values[cell.attrib["r"]] = (text.text or "") if text is not None else int(number.text)
    return root, values


def test_excel_groups_boards_variants_and_maps_manufacturers(tmp_path):
    source = source_csv(tmp_path, [{
        "Used In": "Demo Control Board Alpha (C1,C2,C3)\nDemo Control Board Beta (C4)\n"
                   "Other Board [No Variations] (C5,C6)",
        "Manufacturer 1": "Example Second", "Manufacturer Part Number 1": "00003, 00004 ",
        "Manufacturer 2": "Example Third", "Manufacturer Part Number 2": "00005",
        "Manufacturer 3": "Example Fourth", "Manufacturer Part Number 3": "00006",
        "Manufacturer 4": "Example Overflow", "Manufacturer Part Number 4": "00007"}])
    parsed = parse_export(source)
    report = build_report(parsed)
    assert report["columns"] == [("Demo Control Board", "Alpha"),
                                  ("Demo Control Board", "Beta"), ("Other Board", "[No Variations]")]
    assert report["matrix"][0] == ["00002", "Synthetic component", "Example Co", "Production", "00001",
                                   "Example Second", "00003, 00004 ", "Example Third", "00005",
                                   "Example Fourth", "00006", 3, 1, 2]
    content = workbook_bytes(report)
    root, main_cells = cells(content)
    assert [main_cells[f"{chr(65+i)}1"] for i in range(11)] == PART_HEADERS
    assert main_cells["L1"] == "Demo Control Board"
    assert main_cells["L2"] == "Alpha" and main_cells["M2"] == "Beta"
    assert main_cells["L3"] == 3 and main_cells["N3"] == 2
    assert "M1" not in main_cells
    merges = {e.attrib["ref"] for e in root.findall(f"{{{NS}}}mergeCells/{{{NS}}}mergeCell")}
    assert "L1:M1" in merges and "A1:A2" in merges
    pane = root.find(f"{{{NS}}}sheetViews/{{{NS}}}sheetView/{{{NS}}}pane")
    assert pane.attrib["topLeftCell"] == "C3"
    _, original = cells(content, 3)
    assert "Example Overflow" in original.values() and "00007" in original.values()
    assert original["A4"] == 3  # Report row -> original source record, without matching on CPN.
    assert any(i["code"] == "manufacturer_pair_overflow_in_source_data" for i in report["issues"])
    with ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            ET.fromstring(archive.read(name))


def test_missing_usage_is_blank_and_duplicate_source_parts_stay_separate(tmp_path):
    source = source_csv(tmp_path, [{"Used In": "Demo Default (R1,R2)"}, {},
                                  {"Used In": "Demo Default (R1,R2)"}])
    report = build_report(parse_export(source))
    assert len(report["matrix"]) == 3
    assert [r[-1] for r in report["matrix"]] == [2, None, 2]
    _, values = cells(workbook_bytes(report))
    assert values["L3"] == 2 and "L4" not in values and values["L5"] == 2
    assert values["A3"] == values["A4"] == values["A5"] == "00002"


@pytest.mark.parametrize("refs", ["", "R1,,R2", "R1,R1", "R1 R2"])
def test_ambiguous_reference_lists_are_review_not_zero(tmp_path, refs):
    report = build_report(parse_export(source_csv(tmp_path, [{"Used In": f"Demo Default ({refs})"}])))
    assert report["matrix"][0][-1] == "REVIEW"
    assert report["issues"][0]["severity"] == "error"
    assert report["details"][0][-2] is None


def test_repeated_label_is_not_silently_summed_or_deduplicated(tmp_path):
    source = source_csv(tmp_path, [{"Used In": "Demo Default (R1)\nDemo Default (R2)"}])
    report = build_report(parse_export(source))
    assert report["matrix"][0][-1] == "REVIEW"
    assert len(report["details"]) == 2
    assert report["issues"][0]["code"] == "repeated_board_variant_label"
    assert all(row[-1] == "repeated_board_variant_label" for row in report["details"])


def test_unrecognized_label_is_visible_and_preserved(tmp_path):
    source = source_csv(tmp_path, [{"Used In": "OpaqueLabel"}, {"Used In": "Demo Default (R1)"}])
    report = build_report(parse_export(source))
    assert report["matrix"][0][-1] == "REVIEW"
    assert report["matrix"][1][-1] == 1
    assert report["details"][0][5] == "OpaqueLabel"
    assert report["issues"][0]["code"] == "usage_label_unrecognized"


def test_usage_count_whitespace_and_reference_text_preservation():
    assert split_usage_label("Demo Board Variant_A (R1, R2 ,R3)") == (
        "Demo Board", "Variant_A", "R1, R2 ,R3", 3, "counted")
    assert split_usage_label("Demo Board [No Variations] (C1)")[0:2] == ("Demo Board", "[No Variations]")
    assert split_usage_label("Demo (C1)") is None


def test_excel_literal_text_and_crlf_are_preserved(tmp_path):
    source = source_csv(tmp_path, [{"CPN": "=literal", "Description": 'A & B < C\r\n"quoted"',
                                   "Used In": "Demo Default (R1)"}])
    report = build_report(parse_export(source))
    content = workbook_bytes(report)
    root, values = cells(content)
    assert values["A3"] == "=literal"
    assert values["B3"] == 'A & B < C\r\n"quoted"'
    assert not root.findall(f".//{{{NS}}}f")


def test_excel_mode_updates_summary_and_exit_status(tmp_path):
    source = source_csv(tmp_path, [{"Used In": "Demo Default (R1,R1)"}])
    before = source.read_bytes()
    parsed = parse_export(source)
    original_errors = parsed[-1]["counts"]["error_issues"]
    output = tmp_path / "out"
    summary = write_outputs(output, parsed, excel=True)
    assert (output / "parts_in_use.xlsx").is_file()
    assert summary["excel"]["variant_columns"] == 1
    assert summary["counts"]["error_issues"] == 1
    assert parsed[-1]["counts"]["error_issues"] == original_errors == 0
    assert json.loads((output / "summary.json").read_text())["counts"]["error_issues"] == 1
    assert main([str(source), "--output-dir", str(tmp_path / "cli"), "--excel"]) == 2
    assert source.read_bytes() == before


def test_existing_workbook_is_never_overwritten(tmp_path):
    source = source_csv(tmp_path, [{"Used In": "Demo Default (R1)"}])
    output = tmp_path / "out"
    output.mkdir()
    (output / "parts_in_use.xlsx").write_bytes(b"keep")
    with pytest.raises(InputError, match="already exist"):
        write_outputs(output, parse_export(source), excel=True)
    assert list(output.iterdir()) == [output / "parts_in_use.xlsx"]
    assert (output / "parts_in_use.xlsx").read_bytes() == b"keep"


@pytest.mark.parametrize("value", ["x" * 32768, "bad\x01data"])
def test_excel_incompatible_values_fail_before_writing_and_are_not_truncated(tmp_path, value):
    source = source_csv(tmp_path, [{"Description": value}])
    output = tmp_path / "out"
    with pytest.raises(ReportError):
        write_outputs(output, parse_export(source), excel=True)
    assert not output.exists()


def test_standard_library_only_excel_cli(tmp_path):
    source = source_csv(tmp_path, [{"Used In": "Demo Default (R1,R2)"}])
    output = tmp_path / "out"
    script = Path(__file__).resolve().parents[1] / "parse_parts_in_use.py"
    result = subprocess.run([sys.executable, "-E", "-S", str(script), str(source),
                             "--output-dir", str(output), "--excel"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert cells((output / "parts_in_use.xlsx").read_bytes())[1]["L3"] == 2


def test_many_variants_extend_past_column_z(tmp_path):
    usage = "\n".join(f"Demo Board Variant_{i} (R{i})" for i in range(30))
    report = build_report(parse_export(source_csv(tmp_path, [{"Used In": usage}])))
    root, values = cells(workbook_bytes(report))
    assert values["AO2"] == "Variant_29" and values["AO3"] == 1
    assert "L1:AO1" in {e.attrib["ref"] for e in root.findall(f"{{{NS}}}mergeCells/{{{NS}}}mergeCell")}


def test_malformed_source_width_does_not_become_a_usage_count(tmp_path):
    source = source_csv(tmp_path, [{"Used In": "Demo Default (R1,R2)"}])
    with source.open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow(["part: 00004, manufacturer: Example Co", "Example Co", "00004",
                                     "Demo Default (R1)"])
    report = build_report(parse_export(source))
    assert report["matrix"][1][-1] == "REVIEW"


def test_excel_with_backslash_quote_option_preserves_description(tmp_path):
    source = source_csv(tmp_path, [{"Description": 'Synthetic 0.200" connector, demo',
                                   "Used In": "Demo Default (P1)"}])
    source.write_bytes(source.read_bytes().replace(b'0.200""', b'0.200\\"'))
    output = tmp_path / "out"
    assert main([str(source), "--output-dir", str(output), "--excel", "--backslash-escaped-quotes"]) == 0
    _, values = cells((output / "parts_in_use.xlsx").read_bytes())
    assert values["B3"] == 'Synthetic 0.200" connector, demo'
    assert values["L3"] == 1


def test_excel_extracts_primary_without_a_separate_manufacturer_column(tmp_path):
    source = tmp_path / "synthetic.csv"
    headers = [""] + [h for h in KNOWN if h != "Manufacturer"]
    values = {"Manufacturer Part Number": "part: 00001, manufacturer: Example Components",
              "CPN": "00002", "Description": "Synthetic part", "Lifecycle": "Production",
              "Used In": "Demo Board Default (C1,C2)"}
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerow([values.get(h, "") for h in headers])
    output = tmp_path / "out"
    assert main([str(source), "--output-dir", str(output), "--excel"]) == 0
    content = (output / "parts_in_use.xlsx").read_bytes()
    _, report = cells(content)
    assert report["C3"] == "Example Components" and report["E3"] == "00001"
    assert report["L3"] == 2
    _, original = cells(content, 3)
    assert "Manufacturer" not in original.values()
