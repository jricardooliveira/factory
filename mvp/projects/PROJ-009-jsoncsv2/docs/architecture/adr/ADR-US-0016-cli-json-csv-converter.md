# ADR-US-0016: CLI JSON/CSV Converter

- Date: 2026-06-03
- Story: US-0016
- Status: proposed (pending architecture sign-off)

## Decision
Use a small, stdlib-only CLI split into command dispatch in cli.py and format conversion helpers in converter.py. The CLI should parse a single subcommand (to-csv, to-json, validate) plus input/output file arguments, print usage for unknown commands, and route file I/O through the converter module. For JSON->CSV, load the entire top-level JSON array, require flat dict objects, derive CSV headers from encountered object keys in stable first-seen order, and write one row per object. For CSV->JSON, read the header row and emit a list of dicts using the header names as keys. Validation should attempt to parse the file for the chosen format and print OK on success or the underlying parse error on failure.

## Affected modules
- cli.py
- converter.py
- tests/test_cli.py
- tests/test_converter.py

## Data / API impact
- DB impact: no
- API impact: no
- Migration needed: no

## Constraints for implementation
- Use only the Python standard library.
- Do not add nested JSON flattening, schema inference, or streaming behavior.
- Reject or error on non-array JSON input and non-object array elements for to-csv.
- Preserve CSV header order deterministically using first-seen keys across the JSON array.
- Print plain usage text for unknown commands; do not raise uncaught tracebacks for user input errors.
- Validation must report exactly success vs parse error, not perform transformation.

## Risks
- JSON object key order across multiple records may be ambiguous; a deterministic first-seen union order should be chosen to avoid flaky output.
- Missing keys in later JSON objects can produce sparse CSV rows; this must be handled explicitly by writing empty fields.
- CSV parsing edge cases (embedded commas, quotes, newlines) need standard csv module handling to avoid incorrect round-tripping.
- The exact CLI argument shape is not specified in the story, so implementation should stay minimal and consistent across all commands.

## Breaking changes
- none

## Sensitivity
- none
