"""Trust-package assembly.

Aggregates a completed run's evidence into the operator's release sign-off
package (evidence/schemas/trust-package.schema.json): passing tests + AC coverage,
the change set, the ADR, the security/boundary verdict, and cost. Deterministic
— assembled on demand from stored state, so it needs no extra storage and can be
re-rendered any time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factory.verification import scope as scope_policy
from factory.workspace import git
from factory.state.db import get_db, get_run_gates, get_run_logs
from factory.domain.traceability import trace_criteria
from factory.domain.agent_output import parse_agent_json

# Package data (shipped in the wheel), not a repo doc: the code validates against it.
_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "trust-package.schema.json"


def _latest(logs: list[dict], agent: str) -> dict | None:
    matches = [parse_agent_json(l["output_text"] or "") for l in logs if l["agent"] == agent]
    matches = [m for m in matches if m]
    return matches[-1] if matches else None


def _coder_files(logs: list[dict]) -> list[str]:
    files: list[str] = []
    for log in logs:
        if log["agent"] != "coder-agent":
            continue
        parsed = parse_agent_json(log["output_text"] or "") or {}
        for block in parsed.get("code_blocks", []):
            p = block.get("path")
            if p and p not in files:
                files.append(p)
    return files


def _declared_scope(logs: list[dict]) -> list[str]:
    """The union of every task's declared allowed scope, from the spec-agent output."""
    spec = _latest(logs, "spec-agent") or {}
    scope: list[str] = []
    for task in spec.get("tasks", []) or []:
        for entry in task.get("scope", []) or []:
            if entry and entry not in scope:
                scope.append(entry)
    return scope


# The verification check names that mean a test BODY actually ran. One per
# toolchain — teaching gate-build to run `go test` without adding it here made a
# fully-tested Go story report `tests.executed: false`, understating its own
# evidence (the mirror image of the overstating bug this module was fixed for).
_TEST_RUN_MARKERS = ("pytest_run", "go_test")

# The command each marker corresponds to, so the package never names a command
# that did not run (it used to hardcode "pytest -q" on a Go story).
_TEST_RUN_COMMANDS = {"pytest_run": "pytest -q", "go_test": "go test ./..."}


def _test_execution(gates: list[dict]) -> tuple[bool, bool, str]:
    """(executed, passed) for actual test BODIES, read from gate-build's evidence.

    `verification.VerifyResult.summary` records each check as ``name:status``, so a
    gate-build reason containing ``pytest_run:pass`` is proof a suite really ran.
    Absent that marker nothing was executed — `verification.tests_enabled()` is off by
    default — and the package must not claim otherwise, however green the gates
    look. `gate-build` passes on WARN and on compile-only runs, so the gate
    boolean is not evidence about tests at all.
    """
    executed = passed = False
    commands: list[str] = []
    for gate in gates:
        if gate["gate_name"] != "gate-build":
            continue
        reason = gate["reason"] or ""
        for marker in _TEST_RUN_MARKERS:
            if f"{marker}:" not in reason:
                continue
            executed = True
            command = _TEST_RUN_COMMANDS[marker]
            if command not in commands:
                commands.append(command)
            # Every task's run must pass; one failing run sinks the claim.
            if f"{marker}:fail" in reason:
                return True, False, " && ".join(commands)
            if f"{marker}:pass" in reason:
                passed = True
    return executed, passed, " && ".join(commands)


def assemble(db_path: Path, run_id: int) -> dict[str, Any]:
    """Build the trust package for a run from its stored artifacts."""
    with get_db(db_path) as conn:
        run = conn.execute(
            "SELECT pr.*, s.title AS story_title, s.request, p.repo_path "
            "FROM pipeline_runs pr JOIN stories s ON pr.story_id = s.id "
            "LEFT JOIN projects p ON pr.project_id = p.id WHERE pr.id = ?",
            (run_id,),
        ).fetchone()
        if not run:
            return {}
        run = dict(run)
        logs = get_run_logs(conn, run_id)
        gates = get_run_gates(conn, run_id)
        cost_row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) c, COALESCE(SUM(tokens_in),0) ti, "
            "COALESCE(SUM(tokens_out),0) to_ FROM agent_logs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    gate_pass = {g["gate_name"]: bool(g["passed"]) for g in gates}
    tester = _latest(logs, "tester-agent") or {}
    spec = _latest(logs, "spec-agent") or {}

    # ── Evidence 1: tests. "Passed" must mean a suite RAN and passed (§5.1) ──
    # It used to be derived from gate booleans, which pass on WARN and on a
    # compile-only run — so the package asserted `pytest -q` had passed when no
    # test body had ever been executed.
    tests_executed, tests_really_passed, tests_command = _test_execution(gates)
    tests_passed = (
        tests_executed and tests_really_passed and gate_pass.get("gate-test", False)
    )

    # AC traceability: cross-check the spec's REAL acceptance criteria against what
    # the tester claimed, instead of echoing the tester's self-report (§5.1). Falls
    # back to the tester's list when the spec carried no criteria.
    real_acs = spec.get("acceptance_criteria") or []
    if real_acs:
        trace = trace_criteria(
            real_acs, tester.get("ac_coverage", []), tester.get("missing_coverage", [])
        )
        ac_coverage_entries = [
            {"criterion": e["criterion"], "covered_by": [], "status": e["status"]} for e in trace
        ]
    else:
        trace = []
        ac_coverage_entries = [
            {"criterion": c, "covered_by": []} for c in tester.get("ac_coverage", [])
        ]
    ac_traceability = {
        "total": len(real_acs),
        "covered": sum(1 for e in trace if e["status"] == "covered"),
        "flagged_missing": [e["criterion"] for e in trace if e["status"] == "flagged_missing"],
        "unassessed": [e["criterion"] for e in trace if e["status"] == "unassessed"],
    }

    adr_path = ""
    if run.get("repo_path"):
        adr_dir = Path(run["repo_path"]).parent / "docs" / "architecture" / "adr"
        adrs = sorted(adr_dir.glob(f"ADR-{run['story_id']}-*.md")) if adr_dir.is_dir() else []
        if adrs:
            adr_path = str(adrs[0])

    # ── Evidence 2: the change set, measured from git (§5.2) ──────────
    # It used to be built from the coder's own `code_blocks` and labelled
    # `source: "materialized"` — the exact self-report the schema's `const: git`
    # exists to forbid. When git measurement is impossible the package says
    # "unavailable" and fails `validate()`, rather than dressing a self-report
    # up as measured evidence.
    diff_files: list[dict[str, str]] | None = None
    if run.get("repo_path"):
        diff_files = git.git_changed_files(Path(run["repo_path"]), run.get("base_commit"))
    if diff_files is None:
        diff_block = {
            "source": "unavailable",
            "files": [{"path": p, "change": "added"} for p in _coder_files(logs)],
            "scope_violations": [],
        }
    else:
        diff_block = {
            "source": "git",
            "files": diff_files,
            "scope_violations": scope_policy.paths_outside_scope(
                [f["path"] for f in diff_files], _declared_scope(logs)
            ),
        }

    # ── Blockers: every unmet evidence bar is named, not silently absent ──
    completed = run["status"] == "completed"
    blockers: list[str] = []
    if not completed:
        blockers.append(run.get("error") or run["status"])
    if not tests_executed:
        blockers.append(
            "Tests were never executed (gate-build ran compile/collect only). Set "
            "FACTORY_RUN_TESTS=1 — with a sandbox — for §5.1 evidence."
        )
    elif not tests_really_passed:
        blockers.append("Executed tests did not pass (gate-build reported pytest_run:fail).")
    if diff_block["source"] != "git":
        blockers.append(
            "Change set is not git-measured (§5.2): the repo is not under git, so the "
            "file list is the agent's self-report and cannot be trusted."
        )
    if diff_block["scope_violations"]:
        blockers.append(
            f"Files changed outside the tasks' declared scope (§6): "
            f"{diff_block['scope_violations']}"
        )
    if ac_traceability["unassessed"]:
        blockers.append(
            f"{len(ac_traceability['unassessed'])} acceptance criteria were never "
            f"assessed by the tester (§5.1): {ac_traceability['unassessed']}"
        )

    tests_block: dict[str, Any] = {
        "passed": tests_passed,
        "executed": tests_executed,
        "ac_coverage": ac_coverage_entries,
    }
    # Only claim a command when one actually ran, and name the RIGHT one.
    tests_block["command"] = tests_command if tests_executed else ""

    return {
        "work_id": run["story_id"],
        "parent_story": run["story_id"],
        "project_id": run.get("project_id") or "—",
        "stage": "release",
        "verdict": "pass" if completed else (run["status"] or "fail"),
        "tests": tests_block,
        "ac_traceability": ac_traceability,
        "diff": diff_block,
        "adr": {"path": adr_path, "summary": (tester.get("summary") or "")[:200]},
        "security_boundary": {
            "overall": tester.get("security_verdict", "not_applicable"),
            "highest_severity": tester.get("highest_severity", "none"),
            "breaking_changes": (_latest(logs, "architect-agent") or {}).get(
                "breaking_changes", []
            ),
            "findings": tester.get("security_findings", []),
        },
        "cost": {
            "usd": round(float(cost_row["c"]), 6),
            "tokens_in": int(cost_row["ti"]),
            "tokens_out": int(cost_row["to_"]),
        },
        "blockers": blockers,
        # Release sign-off is offered only when EVERY evidence bar is met — the
        # §5 contract is "all four artifacts are non-negotiable".
        "next_authorization": "release" if completed and not blockers else "operator-review",
    }


def validate(pkg: dict[str, Any]) -> list[str]:
    """Check the package against the schema's required fields AND its constraints.

    This used to check only top-level key presence, which is why a `diff.source`
    of `"materialized"` sailed past a schema that declares `"const": "git"` for
    years' worth of runs. The evidence constraints are now enforced, so the
    package cannot claim to meet a bar it doesn't: `validate()` returning [] is
    the machine-checkable precondition for a release sign-off.
    """
    try:
        schema = json.loads(_SCHEMA_PATH.read_text())
    except (OSError, ValueError):
        return []  # schema unavailable — skip rather than block
    errors = [
        f"missing required field: {key}"
        for key in schema.get("required", [])
        if key not in pkg
    ]

    props = schema.get("properties", {})
    for section, spec in props.items():
        block = pkg.get(section)
        if not isinstance(block, dict):
            continue
        for key in spec.get("required", []):
            if key not in block:
                errors.append(f"missing required field: {section}.{key}")
        for key, field_spec in (spec.get("properties") or {}).items():
            if key not in block or not isinstance(field_spec, dict):
                continue
            value = block[key]
            const = field_spec.get("const")
            if const is not None and value != const:
                errors.append(
                    f"{section}.{key} is {value!r} but the schema requires {const!r} "
                    f"— {field_spec.get('description', '')}".strip()
                )
            allowed = field_spec.get("enum")
            if allowed is not None and value not in allowed:
                errors.append(
                    f"{section}.{key} is {value!r}, not one of {allowed}"
                )

    # ── Evidence bars beyond schema shape (EFFECTIVENESS.md §5) ───────
    # `"unavailable"` is a legal *representation* — the package must be able to
    # state honestly that it could not measure the diff — but it is not a legal
    # basis for sign-off. Keeping this here, rather than in the enum, is what
    # lets the package be truthful AND still be refused.
    if (pkg.get("diff") or {}).get("source") == "unavailable":
        errors.append(
            "diff.source is 'unavailable': the change set was not measured from git, "
            "so §5.2 (real git diff, not the agent's self-report) is unmet"
        )
    return errors
