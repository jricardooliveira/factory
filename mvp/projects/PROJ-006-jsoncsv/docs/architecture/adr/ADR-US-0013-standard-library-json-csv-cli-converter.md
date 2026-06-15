# ADR-US-0013: Standard-library JSON/CSV CLI converter

- Date: 2026-06-03
- Story: US-0013
- Status: proposed (pending architecture sign-off)

## Decision
Use a tiny, single-process CLI built only with the Python standard library. Keep command parsing separate from conversion logic: the entrypoint dispatches to to-csv, to-json, and validate handlers; a conversion module performs pure JSON/CSV transforms; a small I/O layer reads/writes text files or stdin/stdout if already supported by the project’s conventions. For to-csv, parse the top-level JSON array, require flat dict objects, and emit CSV with a stable header order derived from first-seen keys across the array so all fields are preserved. For to-json, use csv.DictReader and emit a JSON array of objects. validate should attempt parsing and print OK on success or the parse exception message on failure. Unknown commands should fall back to usage text and a non-zero exit code.

## Affected modules
- CLI entrypoint module
- argument parsing / usage module
- JSON↔CSV conversion module
- file I/O helpers
- command-level tests

## Data / API impact
- DB impact: no
- API impact: no
- Migration needed: no

## Constraints for implementation
- Use only Python standard library modules.
- Do not support nested JSON flattening or streaming.
- Keep conversion logic pure and testable apart from file I/O.
- Treat unsupported/unknown commands as usage, not silent failure.
- Preserve simple flat-object assumptions; reject or surface parse errors clearly.

## Risks
- Header ordering for JSON→CSV can be ambiguous if objects have different keys; must choose a deterministic order and document it.
- `validate` behavior is underspecified for format detection; the implementation should use the same parsing path as the relevant file type or infer from the command/file context consistently.
- CSV values are inherently text; round-tripping non-string JSON scalars may stringify values when going through CSV.

## Breaking changes
- none

## Sensitivity
- none
