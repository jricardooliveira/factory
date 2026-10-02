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

from factory.domain.agent_output import parse_agent_json
from factory.domain.traceability import trace_criteria
from factory.evidence.adr import adr_dir_for
from factory.state.db import get_db, get_run, get_run_gates, get_run_logs
from factory.state.reports import usage_totals
from factory.verification import scope as scope_policy
from factory.workspace import git
from factory.workspace.layout import EVIDENCE_PATHS
from factory.workspace.sandbox import run_repository

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
    """(executed, passed, command) for actual test BODIES, judged on the FINAL candidate.

    `verification.VerifyResult.summary` records each check as ``name:status``, so a
    gate-build reason containing ``pytest_run:pass`` is proof a suite really ran.
    Absent that marker nothing was executed — `verification.tests_enabled()` is off by
    default — and the package must not claim otherwise, however green the gates
    look. `gate-build` passes on WARN and on compile-only runs, so the gate
    boolean is not evidence about tests at all.

    Each gate-build verifies the CUMULATIVE repo, so per toolchain the newest
    result is the final candidate's. Reading every historical row instead made a
    failure that a retry fixed sink the claim forever; a later frontend-only task
    must not erase the backend's real result either, hence per toolchain.
    """
    latest: dict[str, bool] = {}  # marker -> passed, from the newest build that ran it
    for gate in gates:
        if gate["gate_name"] != "gate-build":
            continue
        reason = gate["reason"] or ""
        for marker in _TEST_RUN_MARKERS:
            if f"{marker}:" in reason:
                latest[marker] = f"{marker}:fail" not in reason
    commands = [_TEST_RUN_COMMANDS[m] for m in _TEST_RUN_MARKERS if m in latest]
    return bool(latest), bool(latest) and all(latest.values()), " && ".join(commands)


def _ac_traceability(
    spec: dict, tester: dict
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(tests.ac_coverage entries, ac_traceability block).

    Cross-checks the spec's REAL acceptance criteria against what the tester
    claimed, instead of echoing the tester's self-report (§5.1). Falls back to the
    tester's list when the spec carried no criteria.
    """
    real_acs = spec.get("acceptance_criteria") or []
    if real_acs:
        trace = trace_criteria(
            real_acs, tester.get("ac_coverage", []), tester.get("missing_coverage", [])
        )
        entries = [
            {"criterion": e["criterion"], "covered_by": [], "status": e["status"]} for e in trace
        ]
    else:
        trace = []
        entries = [{"criterion": c, "covered_by": []} for c in tester.get("ac_coverage", [])]
    traceability = {
        "total": len(real_acs),
        "covered": sum(1 for e in trace if e["status"] == "covered"),
        "flagged_missing": [e["criterion"] for e in trace if e["status"] == "flagged_missing"],
        "unassessed": [e["criterion"] for e in trace if e["status"] == "unassessed"],
    }
    return entries, traceability


def _adr_path(repo: Path | None, story_id: str) -> str:
    if repo is None:
        return ""
    adr_dir = adr_dir_for(repo)
    adrs = sorted(adr_dir.glob(f"ADR-{story_id}-*.md")) if adr_dir.is_dir() else []
    return str(adrs[0]) if adrs else ""


def _diff_block(repo: Path | None, base_commit: str | None, logs: list[dict]) -> dict[str, Any]:
    """The change set, measured from git (§5.2).

    It used to be built from the coder's own `code_blocks` and labelled
    `source: "materialized"` — the exact self-report the schema's `const: git`
    exists to forbid. When git measurement is impossible the package says
    "unavailable" and fails `validate()`, rather than dressing a self-report up as
    measured evidence. A project repository also holds the factory's own evidence
    (ADRs, the artifact chain, earlier trust packages, rules, spec); none of that
    is code an agent changed, so it is excluded.
    """
    diff_files = (
        git.git_changed_files(repo, base_commit, exclude=EVIDENCE_PATHS) if repo else None
    )
    if diff_files is None:
        return {
            "source": "unavailable",
            "files": [{"path": p, "change": "added"} for p in _coder_files(logs)],
            "scope_violations": [],
        }
    return {
        "source": "git",
        "files": diff_files,
        "scope_violations": scope_policy.paths_outside_scope(
            [f["path"] for f in diff_files], _declared_scope(logs)
        ),
    }


def _blockers(
    run: dict, tests_executed: bool, tests_really_passed: bool,
    diff_block: dict[str, Any], traceability: dict[str, Any], *, adr_path: str,
) -> list[str]:
    """Every unmet evidence bar, named rather than silently absent."""
    blockers: list[str] = []
    # A run that STOPPED is a blocker. A run parked at Checkpoint 3 is not: the
    # package is what the operator reads to make that decision, so it lists only
    # evidence gaps — the pending sign-off is `next_authorization`.
    if run["status"] in ("failed", "blocked"):
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
    if not adr_path:
        blockers.append(
            "No design record (ADR) for this story (§5.3): the design decision and its "
            "reasons are not on record."
        )
    if traceability["unassessed"]:
        blockers.append(
            f"{len(traceability['unassessed'])} acceptance criteria were never "
            f"assessed by the tester (§5.1): {traceability['unassessed']}"
        )
    return blockers


_VERDICT_RANK = {"not_applicable": 0, "pass": 1, "warn": 2, "fail": 3}


def _worst(*verdicts: str) -> str:
    known = [v for v in verdicts if v in _VERDICT_RANK]
    return max(known, key=_VERDICT_RANK.__getitem__) if known else "not_applicable"


def _security_boundary(tester: dict, logs: list[dict]) -> dict[str, Any]:
    """Evidence 4 (§5): the tester's post-implementation security verdict joined
    with the pre-implementation boundary review's sub-verdicts, when one ran."""
    review = _latest(logs, "boundary-agent") or {}
    architect = _latest(logs, "architect-agent") or {}
    breaking = list(architect.get("breaking_changes", []))
    breaking += [b for b in (review.get("api_contract") or {}).get("breaking_changes", [])
                 if b not in breaking]
    findings = list(tester.get("security_findings", []))
    block: dict[str, Any] = {
        "overall": _worst(tester.get("security_verdict", "not_applicable")),
        "highest_severity": tester.get("highest_severity", "none"),
        "breaking_changes": breaking,
        "findings": findings,
    }
    if review:
        tenant = (review.get("tenant") or {}).get("verdict", "not_applicable")
        authz = (review.get("authorization") or {}).get("verdict", "not_applicable")
        block["tenant_isolation"] = _worst(tenant)
        block["authorization"] = _worst(authz)
        dims = [(review.get(d) or {}) for d in ("tenant", "authorization", "api_contract",
                                                "security")]
        block["overall"] = _worst(block["overall"], *(d.get("verdict", "") for d in dims))
        findings += [f"boundary review: {f}" for d in dims for f in d.get("findings", [])]
    return block


def assemble(db_path: Path, run_id: int) -> dict[str, Any]:
    """Build the trust package for a run from its stored artifacts."""
    with get_db(db_path) as conn:
        run = get_run(conn, run_id)
        if not run:
            return {}
        logs = get_run_logs(conn, run_id)
        gates = get_run_gates(conn, run_id)
        usage = usage_totals(conn, run_id)

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
    ac_coverage_entries, ac_traceability = _ac_traceability(spec, tester)

    # Where this run's code lives: the project repository, or — for a replay —
    # the scratch clone it was replayed in (see `workspace.sandbox`).
    repo = run_repository(run, db_path)
    adr_path = _adr_path(repo, run["story_id"])
    diff_block = _diff_block(repo, run.get("base_commit"), logs)

    completed = run["status"] == "completed"
    blockers = _blockers(run, tests_executed, tests_really_passed, diff_block, ac_traceability,
                         adr_path=adr_path)

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
        # The schema's verdicts: a stopped run is fail/blocked; otherwise the
        # evidence verdict — warn while any bar is unmet, pass when all are.
        "verdict": {"failed": "fail", "blocked": "blocked"}.get(
            run["status"], "warn" if blockers else "pass"
        ),
        "tests": tests_block,
        "ac_traceability": ac_traceability,
        "diff": diff_block,
        "adr": {"path": adr_path, "summary": (tester.get("summary") or "")[:200]},
        "security_boundary": _security_boundary(tester, logs),
        "cost": {
            "usd": round(float(usage["cost_usd"]), 6),
            "tokens_in": int(usage["tokens_in"]),
            "tokens_out": int(usage["tokens_out"]),
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
    errors = schema_errors(pkg)

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


def schema_errors(pkg: dict[str, Any]) -> list[str]:
    """The package's SHAPE against the schema: required fields, consts, enums.

    Evidence bars (an unmeasured diff, unexecuted tests) are `blockers`, not shape
    errors; gate-release names both, so a malformed package can never read READY.
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
    # Top-level enums too (the verdict used to be an invalid "failed").
    for key, field_spec in props.items():
        allowed = field_spec.get("enum") if isinstance(field_spec, dict) else None
        if allowed is not None and key in pkg and pkg[key] not in allowed:
            errors.append(f"{key} is {pkg[key]!r}, not one of {allowed}")
    return errors
