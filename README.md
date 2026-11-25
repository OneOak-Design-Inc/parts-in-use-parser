# altium-parts-in-use-parser

A small, standalone, offline Python tool for inspecting a manually downloaded
Altium 365 BOM Portal **Parts in Use** CSV. One script, standard library only at
runtime, Python 3.11 or newer. It does not depend on Altium Designer, Altium Develop,
API enrollment, or the separate altium-workspace-reporter project.

## Run

From this repository in PowerShell, using an installed Python 3.11+ interpreter:

```powershell
python --version
python .\parse_parts_in_use.py .\example.csv --output-dir .\reports\my-review
```

If `python` is unavailable, use your Python executable's full path or the Windows
launcher, such as `py -3`, in its place. No package installation is needed to run
the tool. The output directory is created automatically. Use a fresh directory
for each run: existing output files are refused, including a source file whose
name happens to match an output name.

For other confidential inputs, put exports under ignored `data/`:

```powershell
python .\parse_parts_in_use.py .\data\export.csv --output-dir .\reports\review-001
```

Decoding defaults to strict UTF-8, with an optional UTF-8 BOM. No encoding or
delimiter guessing occurs. For an independently known encoding, specify e.g.
`--encoding cp1252`. Invalid bytes fail instead of being replaced. Only comma
delimiters and the observed header names are supported in this milestone.

For the later observed export style, where quotes inside descriptions are written
as `\"`, select that style explicitly:

```powershell
python .\parse_parts_in_use.py .\data\export.csv --output-dir .\reports\review-001 --backslash-escaped-quotes
```

This option decodes only backslash-quote pairs **inside quoted cells** as literal
quote characters. Standard doubled quotes still work. Other backslashes, file
paths and literal `\n` strings remain intact. The mode and number of decoded pairs
are recorded in `summary.json` under `source`. The input file remains unchanged;
output uses standard CSV doubled-quote escaping. Because a backslash immediately
before a quote can also be literal data in standard CSV, the tool never enables
this interpretation automatically. Without the option, standard CSV rules apply.

## What the supplied export contains

Inspection on 2026-11-24 found a 3,290-byte file, two data records and 14 headers.
All bytes are ASCII, so UTF-8 without a BOM decodes them exactly; ASCII-only bytes
cannot establish the producer's encoding for future non-ASCII exports. Record
separators are CRLF, with seven LF line breaks inside quoted `Used In` cells.
Commas inside values are protected by CSV double quotes. The sample does not
exercise embedded double quotes; synthetic tests cover CSV doubled quotes.

The observed header order is:

```text
Manufacturer Part Number
Manufacturer
CPN
Used In
Description
Lifecycle
Manufacturer 1
Manufacturer Part Number 1
Manufacturer 2
Manufacturer Part Number 2
Manufacturer 3
Manufacturer Part Number 3
Manufacturer 4
Manufacturer Part Number 4
```

In this file, column A (`Manufacturer Part Number`) contains the wrapper
`part: <MPN>, manufacturer: <manufacturer>`. Column B (`Manufacturer`) contains a
separate manufacturer string. G/H contain the first numbered manufacturer/MPN
pair. One H cell contains two comma-separated MPN strings and a trailing space.
The meaning and membership of that list are not independently established.
Numbered pairs 2–4 are empty in this sample. Parsing uses header names, so column
order can change.

A later header and record excerpt supplied in chat show 16 columns: the same
named columns plus an empty unnamed first column and `Internal Part Number`.
The latter is preserved as an additional text column, with no identity semantics
assigned. A Description in that excerpt uses a backslash-escaped quote for an
inch mark. That excerpt establishes the need for the explicit quote option;
it does not establish the full file's encoding or completeness.

`CPN` is preserved as exported text; this tool does not establish it as a stable
managed Workspace component or revision identifier. `Used In` contains line-separated
display labels with parenthesized designator-like text and sometimes bracketed
text. Their relationship to project, release, board revision, or assembly variant
identities is unverified. Description and Lifecycle are exported values, with
one Description empty. They do not establish historical metadata snapshots.

## Outputs

| File | Purpose |
| --- | --- |
| `cleaned.csv` | One row per source record, all original columns and values, plus provenance and malformed-row metadata. |
| `manufacturer_fields.csv` | Inspect primary and populated numbered manufacturer/MPN fields in consistent columns, without deduplicating or splitting MPN lists. |
| `usage.csv` | One row per displayed usage line, with its original full `Used In` cell, CPN and source provenance. |
| `summary.json` | Counts, empty/absent/whitespace-only fields, duplicate groups, source hash, format details, issues and interpretation limits. |

All CSV outputs are UTF-8, with standard CSV quoting. Original **cell values**
are preserved, including spaces, leading zeroes, quoted commas, embedded quotes
and embedded newlines. Output quoting and record separators may differ from the
source bytes. The source is opened only for reading.

`cleaned.csv` adds these reserved columns before the original columns:

- `__piu_source_record`: 1-based data record number, excluding the header.
- `__piu_source_line_start` / `__piu_source_line_end`: inclusive physical line
  numbers, counting the header as line 1 and embedded cell newlines as lines.
- `__piu_field_count`: the actual original number of fields.
- `__piu_extra_fields_json`: extra fields beyond the header width, as a JSON list.

Short rows are padded with empty cells for CSV output but remain distinguishable
through field count and the summary's absent-cell counts. Blank records are
retained and flagged. Unexpected columns are retained and reported. Duplicate
records remain separate; only exact full-row duplicates are counted. There is no
component identity matching or manufacturer/MPN deduplication.

`manufacturer_fields.csv` includes source record and line ranges, CPN, `origin`,
`manufacturer_cell_raw`, `mpn_cell_raw`, `manufacturer_text`, `mpn_text` and
`status`. `origin` distinguishes `primary` from numbered MPN columns. The exact
observed primary wrapper is extracted into two text fields. Numbered cell strings
are copied without modification. **No trimming, case folding, spelling changes,
or MPN-list splitting occurs.** Both primary and numbered entries are kept even
if they appear to describe the same part; their record count is not a count of
distinct parts or Part Choices. A comma or newline in a numbered MPN cell produces
`ambiguous_mpn_cell_preserved`, leaving the full value intact. Unrecognized primary
formats preserve the raw value with blank extracted MPN and an explicit error.
Conflicting manufacturer fields and incomplete pairs are also explicit errors.

`usage.csv` separates only real CRLF/LF/CR newlines, preserving each label exactly.
It does not parse parentheses, designators, bracketed labels or literal `\n` text.
Blank lines within a populated usage cell are retained and flagged. An empty or
whitespace-only `Used In` cell produces no usage-line records; its source row is
still in `cleaned.csv` and counted as `rows_without_usage_text`. This means
**unknown usage**, never confirmed zero usage.

Exit codes:

- `0`: outputs written, no error-severity data issues; warnings may exist.
- `2`: outputs written with retained data issues needing review, such as wrong
  field counts, unrecognized primary formats or inconsistent manufacturer fields.
- `1`: fatal input or filesystem failure. Invalid decoding, unsafe headers or
  CSV syntax rejected by Python's strict CSV reader fail before writing outputs.
  Quoting errors are not recovered by guessing row boundaries. A filesystem error
  during output writing can leave partial outputs; use a fresh directory to retry.

CSV syntax failures include the underlying reader's reason and physical line
numbers without printing cell contents. A record can span multiple lines, so the
record's starting line can differ from the line where parsing stops. Inspect the
original header and the full affected record before changing quoting rules. For
confidential data, provide a sanitized excerpt preserving commas, quotes and
line breaks; do not remove arbitrary quotes or resave the original through Excel.

An unnamed first column is accepted only when every present cell in that column
is exactly empty. Its original blank header and empty cells remain in
`cleaned.csv`; no column name is invented and no values are shifted. The summary
records `empty_leading_column_preserved`, retains the blank entry in source
headers, and uses the empty string key (`""`) for that column's cell counts.
Any data in that unnamed column, including whitespace, fails before outputs are
written and identifies the physical line. Other blank headers, duplicate headers,
missing required headers, or headers beginning with `__piu_` fail explicitly.
The six unnumbered headers are required. Numbered pairs
1–4 may be absent and are reported; later/unrecognized columns are preserved but
not interpreted. Missing cell counts do not claim that an entire column's absence
was an empty value. Format newline counters in the summary include quoted cells.

## Inspect and filter

For a local manufacturer search after parsing:

```powershell
Import-Csv .\reports\my-review\manufacturer_fields.csv |
    Where-Object { $_.manufacturer_text -like '*your search text*' } |
    Select-Object source_record, CPN, origin, manufacturer_text, mpn_text, status
```

For displayed usage labels associated with an exact exported CPN string:

```powershell
Import-Csv .\reports\my-review\usage.csv |
    Where-Object { $_.CPN -eq 'your exported CPN' } |
    Select-Object source_record, usage_label_raw
```

These are text filters, not identity joins. If opening a CSV in a spreadsheet,
import identifier/MPN columns as text to keep that application's type conversion
from removing leading zeroes. Excel workbook output is outside this milestone.

## Validation and remaining uncertainty

On 2026-11-24:

- **40 offline pytest cases passed on Python 3.12.14 / Windows.** Tests cover
  both observed field layouts, reordered/extra columns, leading zeroes, spaces,
  quoted commas/doubled quotes, multiline and large cells, explicit encodings,
  missing/blank values, malformed widths, duplicate rows, unsafe headers, invalid
  decoding/quoting, manufacturer conflicts, incomplete pairs, source/output
  protection, and CLI execution with site packages disabled. Regression checks
  also cover an entirely empty unnamed first column, header/value preservation,
  alignment of both manufacturer layouts, original source line numbers, and
  rejection of populated unnamed columns or additional blank headers. CSV syntax
  diagnostics are checked for header errors, unterminated fields and multiline
  record errors, including not exposing cell contents in those messages. The
  explicit backslash-quote mode is checked with a synthetic version of the
  16-column structure, multiline usage and an inch-mark-style quote; tests also
  cover mixed quote styles, preservation of unrelated backslashes and literal
  backslash-n text, unchanged source bytes, output round-trip values, and strict
  rejection of malformed quoting with the option enabled.
- The owner reported that a second export has an empty first column. Support for
  that structure was verified with synthetic data, including a UTF-8 BOM and
  multiline usage cells. That second real export is not available in this checkout
  and has not yet been rerun here.
- **The supplied real sample ran on Python 3.14.6 with site packages disabled.**
  It produced two cleaned rows, three manufacturer-field records and nine usage
  label lines, with zero error issues and one ambiguous-MPN warning. There were
  no exact duplicate source rows. Source line ranges were 2–8 and 9–10.
- All original headers/cell values were compared to `cleaned.csv` after reading
  it back with `csv`; they matched exactly. SHA-256 before inspection and after
  output generation matched. Real outputs are under ignored `reports/sample-review/`.
- Git ignore checks confirmed the supplied `example.csv` and generated reports
  are excluded. Only the artificial `tests/fixtures/synthetic.csv` is intended
  for version control. No real part, client or project labels appear in it.

The sample does not prove complete Part Choices, managed library identities,
unused components, stable project/release IDs, quantities, DNP status, historical
revisions or export completeness. Usage-line counts are not fitted quantities or
manufacturing demand. There has been no wider export validation, manual comparison
against the portal, or Python 3.11 execution. The parser reads the file and output
records into memory; very large exports have not been benchmarked. Other export
schemas and delimiters require inspecting new evidence before support is added.

## Development

Optional isolated test environment (pytest is the only development dependency):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

The Codex sandbox's default temporary directory was unavailable during validation.
The latest successful full-suite run used
`--basetemp=reports/pytest-quote-style-check-2` after
creating `reports/`. If needed, choose a disposable test directory under this
repository with an existing parent; pytest deletes its basetemp on reuse.

Keep confidential exports in ignored `data/`, and generated files in ignored
`reports/`. CSV/JSON/spreadsheet files are ignored by default, with one explicit
exception for the synthetic fixture. Do not force-add real evidence. No network,
authentication, database, GUI, web server or Workspace changes are implemented.

Suggested initial commit message:
`Add offline Parts in Use CSV parser with preserved values and usage labels`
