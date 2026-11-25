# altium-parts-in-use-parser

A small, standalone, offline Python tool for inspecting a manually downloaded
Altium 365 BOM Portal **Parts in Use** CSV. A small CLI and optional Excel-report
module use only the standard library at runtime, Python 3.11 or newer.
It does not depend on Altium Designer, Altium Develop,
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
| `parts_in_use.xlsx` (with `--excel`) | Requested part columns and board/variant count matrix, plus usage-detail and original-source sheets. |

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

The CSV output `usage.csv` separates only real CRLF/LF/CR newlines, preserving each label exactly.
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
The five unnumbered headers other than `Manufacturer` are required. The separate
`Manufacturer` column is optional: both the primary manufacturer and MPN come
from the `manufacturer:` and `part:` values in `Manufacturer Part Number`.
No missing source column is synthesized. If a separate manufacturer column is
present, its original values remain available and conflicts are still reported.
Its absence is recorded in `absent_optional_columns`, without an error or warning.
Numbered pairs
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
from removing leading zeroes. With `--excel`, the workbook already stores these
identifiers as text.

## Excel board/variant report

The owner requested and approved this layout after testing the CSV parser.
Add `--excel` to also generate `parts_in_use.xlsx`:

```powershell
python .\parse_parts_in_use.py .\data\export.csv --output-dir .\reports\review-002 --backslash-escaped-quotes --excel
```

Update both `parse_parts_in_use.py` and `excel_report.py` in your local checkout.
No additional package installation or running Excel application is required.
The existing CSV/JSON files are still produced. Use a new output directory.

The first sheet, **Parts in Use**, has two header rows. The fixed columns are:

```text
CPN, Description, Manufacturer 1, Lifecycle, MPN 1,
Manufacturer 2, MPN 2, Manufacturer 3, MPN 3, Manufacturer 4, MPN 4
```

Each board name is merged across its variant columns in the first header row;
variants are in the second row. The fixed column headings span both header rows.
Header rows and the first two identifying columns remain visible when scrolling.
Alternating row fills extend across the full data width, including numeric usage
counts and blank cells. Thin vertical separators distinguish columns; REVIEW
cells retain their warning highlight. Styled blanks remain empty, not zero.
Board/variant groups follow first appearance in the export. Each source record
has its own report row: duplicate CPNs or repeated source rows are not merged.

Manufacturer/MPN mapping, agreed with the owner:

| Output | Export source |
| --- | --- |
| Manufacturer 1 / MPN 1 | `manufacturer:` / `part:` values extracted from the primary `Manufacturer Part Number` wrapper. |
| Manufacturer 2 / MPN 2 | `Manufacturer 1` / `Manufacturer Part Number 1`. |
| Manufacturer 3 / MPN 3 | `Manufacturer 2` / `Manufacturer Part Number 2`. |
| Manufacturer 4 / MPN 4 | `Manufacturer 3` / `Manufacturer Part Number 3`. |

The export's fourth numbered pair remains on **Source data**; populated overflow
adds a warning. Comma-separated MPN cells remain intact. No preference ranking,
identity matching, or deduplication is inferred.

The owner specified on 2026-11-25 that **board/project names never contain
spaces**. Split the usage label at its **first space**: the first word is the
board name, and everything after it, before the final reference list, is the
variant. Thus `Demo Production Build One (R1,R2)` means board `Demo`, variant
`Production Build One`, and count 2. Multiword variants, their internal spacing,
and bracketed values such as `[No Variations]` are preserved. A missing or
whitespace-only variant is flagged. Board names containing spaces are outside
this explicitly chosen rule. These names remain display labels, not verified
Workspace project, release or revision identifiers. The rule is also recorded
in `summary.json` under `excel.usage_label_rule`.

Counts are the number of nonempty comma-separated reference tokens in one
source row's board/variant entry. Surrounding whitespace in tokens is ignored
only for counting; original text is retained. Ranges are not expanded. Empty
lists, repeated reference tokens, whitespace-separated references, unrecognized
usage syntax, malformed source rows and repeated entries for the same board and
variant are flagged. Repeated entries are not silently summed, since the export
does not establish whether they represent distinct designs, duplicate labels or
overlapping lists.

- A number is a count of **listed reference designators**, not verified fitted
  quantity, production demand or a sum across mutually exclusive variants.
- A blank means there was no usable exported entry for that row/column; it is
  **not confirmed zero usage**.
- `REVIEW` means invalid or ambiguous data prevents assigning a reliable count.
  See `summary.json` and **Usage details** for the affected source record.

**Usage details** retains each original usage line with the interpreted board,
variant, reference list, per-line count and status. **Source data** retains every
original column/value, source record and line range, plus the corresponding main
report row number. This includes `Internal Part Number`, overflow manufacturer
fields and unexpected columns. The source filename, hash and count definitions
appear there once. Very long source cells remain complete; Excel row-height
limits may require inspecting the cell contents in the formula bar.

Counts are static results of this run; regenerate the report after changing the
CSV. The report does not use workbook formulas to reinterpret edited source cells.
Excel-incompatible control characters, strings over Excel's cell text limit, or
worksheet-size limits fail before any output files are written. Values are never
silently truncated. Excel-specific data issues are included in `summary.json`
and in the CLI error/warning totals; error-severity issues produce exit code 2.

## Validation and remaining uncertainty

Validation record:

- **63 offline pytest cases passed on Python 3.12.14 / Windows on 2026-11-25.** Tests cover
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
  rejection of malformed quoting with the option enabled. Excel tests cover
  merged board/variant headers, multiword variants, bracketed variants, manufacturer
  mapping/overflow, numeric counts, missing versus zero, repeated/invalid usage,
  source traceability, text identifiers, literal leading `=`, CRLF preservation,
  more than 26 columns, cell limits, output protection, backslash-quoted input,
  and execution with site packages disabled. Regression tests also verify that
  omitting the separate `Manufacturer` column still extracts both primary values
  from the wrapper, preserves original headers/cells, keeps numbered pairs, and
  produces Excel counts; unrecognized primary text is not guessed. The optional
  column case has been tested synthetically, not against the unavailable full
  real export. Regression checks for the new first-space split verify that two
  multiword variants share one merged board header, counts remain correct, and
  original usage lines, internal variant spacing and parentheses are preserved.
  Saved workbook checks also verify that row fills extend through numeric and
  empty usage cells, with vertical column borders and no invented zero values.
- On 2026-11-25, the full-width banding and column separators were visually checked
  on all three sheets of a synthetic workbook. An independent openpyxl read-back
  confirmed fills, borders, blank values, numeric counts, text identifiers and
  merged headers. Native Excel validation remains outstanding.
- The owner reported that a second export has an empty first column. Support for
  that structure was verified with synthetic data, including a UTF-8 BOM and
  multiline usage cells. That second real export is not available in this checkout
  and has not yet been rerun here. The owner subsequently reported a successful
  CSV run with 209 source records, 215 manufacturer-field records, 1,432 usage
  label lines, zero errors and two warnings. These are user-reported results;
  the warnings and counts have not been independently compared with that file.
- A synthetic three-part Excel example of the original layout was generated locally on
  2026-11-24. All three sheets
  were rendered and visually reviewed with Artifact Tool. Merged ranges, numeric
  counts, text identifiers/leading zeroes, missing cells and freeze panes were
  independently read back with openpyxl (validation only, no runtime dependency).
  The preview engine required a display-only workaround for leading-zero text;
  the delivered workbook's stored strings and types were verified separately.
  No native Excel application validation or full real-export Excel comparison
  has been performed.
- **The supplied real sample ran on Python 3.14.6 with site packages disabled on 2026-11-24.**
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
`--basetemp=reports/pytest-banding-check` after
creating `reports/`. If needed, choose a disposable test directory under this
repository with an existing parent; pytest deletes its basetemp on reuse.

Keep confidential exports in ignored `data/`, and generated files in ignored
`reports/`. CSV/JSON/spreadsheet files are ignored by default, with one explicit
exception for the synthetic fixture. Do not force-add real evidence. No network,
authentication, database, GUI, web server or Workspace changes are implemented.

Suggested initial commit message:
`Add offline Parts in Use CSV parser with preserved values and usage labels`
