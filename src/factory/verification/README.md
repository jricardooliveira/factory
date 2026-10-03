# verification

## Responsibility

Non-LLM, deterministic judgement of what the coder produced. `verify_changes` checks that the
files the coder materialized parse, compile or typecheck (per toolchain), and `scope.py` holds the
two pure scope policies (declared-vs-changed, changed-vs-task-scope). Nothing here calls a model,
and nothing here measures git: measuring lives in `factory.workspace.git`, this package only
judges the result.

## Modules

| Module | What it does |
|---|---|
| `__init__.py` | `verify_changes(written_paths, *, root) -> VerifyResult`: picks checks by file extension. Re-exports `VerifyCheck`, `VerifyResult`, `tests_enabled`. |
| `base.py` | `VerifyCheck(name, status, detail)` with status `pass`/`fail`/`warn`/`skip`; `VerifyResult` (`passed`, `verdict`, `summary`); `run_command` (subprocess with `stdin=DEVNULL`); `tests_enabled()` (`FACTORY_RUN_TESTS`); timeouts `COMMAND_TIMEOUT=60`, `TEST_TIMEOUT=180`, `BUILD_TIMEOUT=180`. |
| `python.py` | `py_compile_check`, `static_import_check` (AST only), `run_tests` (opt-in; a dependency or pytest missing from the product interpreter is a `warn`, never a pass), `has_tests`, `is_py_test`, `_classify_collect_failure`. |
| `go.py` | `go_module_dirs` (nearest `go.mod` per file), `go_build`, `go_parse` (`gofmt -e`, no module needed), `go_vet`, `run_go_tests` (opt-in), `_classify_go_failure`. |
| `typescript.py` | `node_check` (`node --check` for `.js/.mjs/.cjs`), `tsc_check` (`tsc --noEmit -p <dir>` per owning `tsconfig.json`), `_resolve_tsc` (prefers the project's own `node_modules/.bin/tsc`). |
| `scope.py` | `declared_scope_mismatch` + `scope_note` (declared `code_blocks` vs git-measured change set), `paths_outside_scope` (change set vs a task's `scope`), `is_test_path`, `_MANIFEST_NAMES`. |

## How it works

`verify_changes` splits the written paths by suffix and appends one `VerifyCheck` per check.
`VerifyResult.verdict` is `fail` if any check failed, else `warn` if any warned, else `pass`; no
verifiable files yields a single `verify: skip`.

- **Python:** `py_compile` always, then `static_import_check`, which parses (never imports) the
  changed files and fails on a local module that does not exist or a name the local module does not
  define. Third-party imports are not judged. The test suite runs only when `tests_enabled()` and
  `has_tests(root)`.
- **Go:** `go_build` over each module dir. If no `go.mod` is found it warns and `go_parse` runs
  `gofmt -e` as a fallback so Go written before a manifest is still syntax-checked. `go_vet` (and
  opt-in `go test`) run only if the build passed. `_classify_go_failure` turns an unresolvable
  import under the module's own path into `fail`, and network/missing-dependency noise into `warn`.
- **TypeScript:** each changed `.ts/.tsx` is mapped to its nearest `tsconfig.json`; tsc runs once
  per project with `-p`. No tsconfig is a `warn`; "Cannot find module" is a `warn` (deps not
  installed); real type errors fail. (Bare `tsc --noEmit` with no `-p` printed help text and
  exited 1, a false FAIL.)
- **Scope:** `declared_scope_mismatch(changed, claimed)` returns `(unclaimed, missing)`; the coder
  node turns unclaimed files into a BLOCKED gate-build. `paths_outside_scope` strips a leading
  `repo/` from both sides, ignores test paths and toolchain manifests (`go.mod`, `package.json`,
  `__init__.py`, ...), and treats an empty scope as "forbids nothing". Entries may be a directory
  prefix, an exact path or an fnmatch glob.

Invariants:

- **A missing toolchain FAILS its files, never skips them** (`node`, `gofmt`, `go` for build,
  `tsc`: "not installed ... see `factory doctor`"). A check that cannot run cannot earn a pass.
  Exceptions that do `skip`: `go_vet`/`go_test` without `go`, and pytest not installed for the
  opt-in test run.
- **Nothing the coder wrote is executed by default.** `pytest --collect-only` is never called
  (collection imports test modules). Pytest and `go test` run only with `FACTORY_RUN_TESTS=1|true|yes|on`. This is a
  subprocess with a timeout, not an OS sandbox.
- `scope.py` judges in one path space (`repo/` stripped, `__init__.py` structural). The coder node
  refuses out-of-scope `code_blocks` before they are written (`pipeline/nodes/coder.py::_outside_scope`);
  the trust package also reports `scope_violations`.

## Imports / imported by

- Imports only `factory.verification.*` and the standard library (`scope.py` has no factory imports).
- `verify_changes` and `scope` are used by `pipeline/nodes/coder.py`; `scope` is also used by
  `evidence/trust_package.py` (`paths_outside_scope`, `is_test_path`). `preflight/doctor.py`
  references it only in a comment (the toolchains it shells out to).

## Adding things

A new toolchain needs all three together (see `docs/ARCHITECTURE.md` -> "A new toolchain"):

1. `verification/<toolchain>.py` with checks returning `VerifyCheck`, wired into `verify_changes`.
   Return `fail` when the tool is absent.
2. Its test-run marker (like `pytest_run`, `go_test`) in `evidence.trust_package._TEST_RUN_MARKERS`
   and `_TEST_RUN_COMMANDS`, or the trust package misreports whether tests ran.
3. Any manifest the toolchain structurally requires in `scope._MANIFEST_NAMES`, or the scope check
   flags every story for a file it cannot avoid creating.

Also add the tool to the `factory doctor` toolchain list (`preflight/doctor.py`). Tests live in
`tests/verification/`. New deterministic checks belong here, not in an agent prompt.
