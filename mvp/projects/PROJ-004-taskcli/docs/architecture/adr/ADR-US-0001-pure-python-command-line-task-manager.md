# ADR-US-0001: Pure Python command-line task manager

- Date: 2026-06-02
- Story: US-0001
- Status: proposed (pending architecture sign-off)

## Decision
Use a small, single-file-or-few-file CLI with one storage layer responsible for creating/loading/saving tasks.json and one command layer responsible for parsing subcommands and rendering output. Persist tasks as JSON in a simple object containing an array of task records. Compute the next task id as max(existing ids) + 1 so ids remain unique after deletions. Keep all behavior standard-library-only and local-first, with automatic file initialization on any command.

## Affected modules
- CLI entrypoint / argument parsing module
- task storage module
- task model / schema definition
- task listing and formatting module
- summary/reporting logic

## Data / API impact
- DB impact: no
- API impact: yes
- Migration needed: no

## Constraints for implementation
- Use only the Python standard library.
- Always create tasks.json automatically if missing.
- Do not reuse deleted ids; always allocate from max existing id + 1.
- Validate priority and status values before writing.
- Persist every mutation immediately after add, done, and delete.
- Keep output stable and human-readable for list and summary.
- Avoid introducing any database, network, or third-party dependency.

## Risks
- Concurrent CLI runs could race and overwrite changes because storage is file-based and unlocked.
- Manual edits to tasks.json could introduce malformed data; loader should fail clearly or recover only from missing file, not arbitrary corruption.
- Filter behavior must be precisely defined for invalid values to avoid confusing list output.

## Breaking changes
- none

## Sensitivity
- none
