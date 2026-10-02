# preflight/

## Responsibility

`factory doctor`: a live-environment preflight that fails before a run spends tokens. It checks that
`opencode` is installed, that each distinct tier model really answers, that optional toolchains exist,
and that `$FACTORY_HOME`, its database and the registered products are usable. It is its own package
because `selftest/` is offline and zero-token by contract, while the doctor makes real (small, paid) model calls.

## Modules

| Module | What it does |
|---|---|
| `__init__.py` | Docstring only; no re-exports. Import `factory.preflight.doctor`. |
| `doctor.py` | `run_doctor(*, offline=False, which=shutil.which, checkout=None) -> DoctorReport`; dataclasses `Check(name, status, detail, blocking)` and `DoctorReport` (`.passed`); helpers `_probe`, `_probe_agent`, `_escalation_check`, `_workspace_checks`, `_project_check`, `_legacy_state_check`; constants `PROBE_PROMPT`, `PROBE_TIMEOUT_SECS` (120), `TOOLCHAINS`, `LEGACY_STATE_DIRS`; `repo_root()` = `agent_config.location.checkout_root()`. |

## How it works

`run_doctor` appends checks in this order:

1. `opencode` on PATH (blocking).
2. `tiers`: loads `agents/tiers.toml` via `tiers.config()` (blocking on parse/IO error), plus a non-blocking
   `tier escalation` warning when an `escalate_on_retry` hop resolves to the same model as the first attempt.
3. One `model <id>` check per **distinct** model (`tiers.distinct_models()`, honouring `FACTORY_TIER_*`
   overrides). With `offline=True` the status is `skip`; without opencode it is `fail`; otherwise `_probe`
   calls `adapters.opencode.run_agent` with `PROBE_PROMPT`. An empty answer or one starting with `ERROR`
   fails, because opencode can exit 0 after streaming only an error event.
4. `go`, `node`, `tsc` (warn only, non-blocking): `verification` needs them and a skipped check is how a false PASS ships.
5. Workspace: `$FACTORY_HOME` must be a writable directory; a missing `factory.db` is fine (created on first
   run); an existing one is read through `state.reports.read_only_summary`. Each registered project gets a
   non-blocking check: directory exists and `.opencode` resolves to THIS checkout's `.opencode`.
6. `legacy state` warning if a pre-`$FACTORY_HOME` `factory.db` is in the checkout (`mvp/` or root).

`DoctorReport.passed` is false only if a **blocking** check has status `fail`; the CLI maps that to a non-zero exit.
The doctor is read-only: it never creates the home, opens the DB for writing, or repairs a link (it prints the `ln -sfn` fix).
The `which` and `checkout` parameters exist so tests can inject fakes.

## Imports / imported by

- Imports: `factory.adapters.opencode.run_agent`, `factory.agent_config` (`tiers`, `location`),
  `factory.state.reports.read_only_summary`, `factory.workspace.layout`.
- Imported by: `interfaces/cli/selftest.py` (`doctor_command`, lazy import, wired as `doctor` in `cli/main.py`).
  Rendering is `interfaces/render/selftest.py`. Tests: `tests/preflight/test_doctor.py`,
  `tests/interfaces/cli/test_doctor_cli.py`.

## Gotchas / where to add things

- Tests must patch `factory.preflight.doctor.run_agent`: a probe is a real, paid model call. Never probe from a test.
- New toolchain the verifier shells out to: add it to `TOOLCHAINS` (warn-only) and to `verification/`.
- New environment check: add a function returning `Check`/`list[Check]` and append it in `run_doctor`.
  Choose `blocking=True` only when a run cannot work without it.
- The probe agent is arbitrary (`--model` is passed explicitly); `_probe_agent` just picks one on the tier.
- CLAUDE.md and `docs/ARCHITECTURE.md` may still say `selftest/doctor.py`; the code lives here.
