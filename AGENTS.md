# Repository guidance

This standalone offline tool parses manually downloaded Altium 365 Parts in Use
CSV exports. Keep it substantially simpler than a Workspace reporting app.

- Inspect a representative export before adding schema or parsing rules. Use
  column names, never Excel positions. Preserve unexpected columns.
- Use Python 3.11+ and the standard library at runtime. Start with one script.
- Never modify source files, silently discard malformed rows, merge duplicates,
  guess identities, or convert identifiers to numbers.
- Preserve original values and physical source line numbers. Missing data is not
  zero usage. Usage labels are not stable project/release/variant identifiers.
- Distinguish exported manufacturer parts from managed library components. Do
  not infer complete Part Choices, unused components, quantities, historical
  revisions, or manufacturing demand.
- Keep normalization explicit and retain source strings. Do not split ambiguous
  comma-separated MPN values without new evidence establishing their semantics.
- No network access, authentication, database, browser automation, GUI, or server.
  Agree on a layout with the owner before adding Excel output.
- Treat inputs and reports as confidential. Keep them in ignored `data/` and
  `reports/`; the original `example.csv` is also ignored. Commit only synthetic
  or explicitly sanitized fixtures. Never copy real values into docs or tests.
- Run focused tests and `python -m pytest -q` for behavior changes. Record what
  has been tested and what remains uncertain in README.md.
- Include a suggested commit message in every change summary.
- Work only in this repository; do not modify altium-workspace-reporter.
