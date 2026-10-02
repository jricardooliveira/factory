"""Capture: turn a real run (or an incident) into a permanent replay case."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from factory.selftest.evals.cases import default_cases_dir
from factory.state.db import get_db, get_run, get_run_logs


def capture_case(
    db_path: Path,
    run_id: int,
    name: str,
    *,
    description: str = "",
    cases_dir: Path | None = None,
    expect_status: str | None = None,
) -> Path:
    """Freeze a real run's agent outputs into a replayable eval case.

    This is what makes the playbook's "every production incident becomes a
    permanent eval" affordable: the outputs are already stored verbatim, so a
    case costs nothing to create and nothing to re-run.
    """
    cases_dir = cases_dir or default_cases_dir()
    with get_db(db_path) as conn:
        run = get_run(conn, run_id)
        if not run:
            raise ValueError(f"No run found with id #{run_id}")
        logs = get_run_logs(conn, run_id)

    outputs: dict[str, Any] = {}
    for log in logs:
        agent = log["agent"]
        text = log["output_text"] or ""
        if not text.strip():
            continue
        if agent == "coder-agent":
            # stage_type carries the task id (or "remediation") the row belongs to.
            slot = log["stage_type"] if log["stage_type"] not in (None, "agent") else "T-0001"
            outputs.setdefault(agent, {})[slot] = text
        else:
            # Later rows win: the last output is the one the run actually acted on.
            outputs[agent] = text

    if not outputs:
        raise ValueError(f"Run #{run_id} has no agent outputs to capture")

    payload = {
        "name": name,
        "description": description or f"captured from run #{run_id} ({run['story_id']})",
        "source_run_id": run_id,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": True,
        "agent_outputs": outputs,
        "expect": {"status": expect_status or run["status"]},
    }
    cases_dir.mkdir(parents=True, exist_ok=True)
    path = cases_dir / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
