"""Trust-package assembly.

Aggregates a completed run's evidence into the operator's release sign-off
package (docs/factory/trust-package.schema.json): passing tests + AC coverage,
the change set, the ADR, the security/boundary verdict, and cost. Deterministic
— assembled on demand from stored state, so it needs no extra storage and can be
re-rendered any time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factory.state.db import get_db, get_run_gates, get_run_logs
from factory.traceability import trace_criteria
from factory.utils import parse_agent_json

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "factory" / "trust-package.schema.json"


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
    tests_passed = gate_pass.get("gate-build", False) and gate_pass.get("gate-test", False)

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

    completed = run["status"] == "completed"
    return {
        "work_id": run["story_id"],
        "parent_story": run["story_id"],
        "project_id": run.get("project_id") or "—",
        "stage": "release",
        "verdict": "pass" if completed else (run["status"] or "fail"),
        "tests": {
            "passed": tests_passed,
            "command": "pytest -q",
            "ac_coverage": ac_coverage_entries,
        },
        "ac_traceability": ac_traceability,
        "diff": {
            "source": "materialized",
            "files": [{"path": p, "change": "added"} for p in _coder_files(logs)],
        },
        "adr": {"path": adr_path, "summary": (tester.get("summary") or "")[:200]},
        "security_boundary": {
            "overall": tester.get("security_verdict", "not_applicable"),
            "highest_severity": tester.get("highest_severity", "none"),
            "breaking_changes": [],
            "findings": tester.get("security_findings", []),
        },
        "cost": {
            "usd": round(float(cost_row["c"]), 6),
            "tokens_in": int(cost_row["ti"]),
            "tokens_out": int(cost_row["to_"]),
        },
        "blockers": [] if completed else [run.get("error") or run["status"]],
        "next_authorization": "release" if completed and tests_passed else "operator-review",
    }


def validate(pkg: dict[str, Any]) -> list[str]:
    """Check the package has the schema's required top-level fields. Best-effort."""
    try:
        schema = json.loads(_SCHEMA_PATH.read_text())
    except (OSError, ValueError):
        return []  # schema unavailable — skip rather than block
    required = schema.get("required", [])
    return [f"missing required field: {key}" for key in required if key not in pkg]
