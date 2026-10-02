"""PIPELINE.md — the boss's committed record of a run (the brief's "local pipeline file").

INTENT/SPEC/PLAN say what was asked and planned; the trust package exists only
when a run COMPLETES. The database holds everything else, and it is gitignored
and local — so a run that failed, was refused by the boss or is waiting for the
operator left nothing in the product repo saying why. This file is that record,
rewritten each time a run stops: every authorization, agent verdict and gate
verdict in order, the blockers, any warnings, and the one next authorized step.

Rendered from the stored record only (`progress.run_timeline` + the run row), so
it never says more than the database can back.
"""

from __future__ import annotations

from pathlib import Path

from factory.agent_config.settings import settings
from factory.agent_config import tiers
from factory.domain.budget import story_spend
from factory.evidence.artifacts import work_dir_for
from factory.evidence.progress import TimelineEvent, run_timeline
from factory.state.db import (
    get_db,
    get_pending_human_gate,
    get_run,
    get_run_authorizations,
    usage_rows,
)

FILENAME = "PIPELINE.md"

_ICONS = {"done": "✅", "failed": "❌", "waiting": "⏸️", "info": "•"}
_CELL_MAX = 220


def _cell(text: str) -> str:
    """One markdown table cell: no pipe or newline may break the row."""
    flat = " ".join((text or "").split()).replace("|", "\\|")
    return flat if len(flat) <= _CELL_MAX else flat[: _CELL_MAX - 1] + "…"


def _when(iso: str | None) -> str:
    return (iso or "")[:19].replace("T", " ") or "—"


def _next_step(run: dict, pending_gate: dict | None) -> str:
    run_id, status = run["id"], run["status"]
    if status == "waiting_human":
        gate = pending_gate["gate_name"] if pending_gate else "a checkpoint"
        return (
            f"The operator decides at {gate}: `factory approve {run_id}` to accept, or "
            f"`factory reject {run_id} \"<your answers>\"` to send it back."
        )
    if status == "completed":
        return (
            "None — the story is complete. Release evidence: "
            f"`docs/releases/run-{run_id}-trust-package.json`."
        )
    if status in ("failed", "blocked"):
        return (
            f"None — the run stopped ({status}). Nothing proceeds until the cause under "
            "Blockers is fixed and a new run is started."
        )
    return f"In progress at {run.get('current_stage') or 'an unknown stage'}."


def _trail_row(event: TimelineEvent) -> str:
    outcome = f"{_ICONS.get(event.status, '•')} {event.detail}".strip()
    return f"| {_when(event.when)} | {event.kind} | {_cell(event.label)} | {_cell(outcome)} |"


def render_pipeline_record(db_path: Path, run_id: int) -> str | None:
    """The PIPELINE.md text for one run, or None if the run does not exist."""
    with get_db(db_path) as conn:
        run = get_run(conn, run_id)
        if not run:
            return None
        pending = get_pending_human_gate(conn, run_id)
        authorizations = get_run_authorizations(conn, run_id)
        spend = story_spend(usage_rows(conn, story_id=run["story_id"]), tiers.config().prices)
    events = run_timeline(db_path, run_id)

    blockers: list[str] = []
    if run["status"] in ("failed", "blocked") and run.get("error"):
        blockers.append(f"- {run['error']}")
    if pending:
        blockers.append(f"- Waiting for the operator at **{pending['gate_name']}**:")
        questions = (pending.get("human_questions") or "").strip() or "(no question text)"
        blockers += [f"  > {line}" if line.strip() else "  >" for line in questions.splitlines()]
    for a in authorizations:
        if not a["allowed"]:
            blockers += [f"- The boss refused {a['stage']}: missing {m}" for m in a["missing"]]

    warnings: list[str] = []
    for a in authorizations:
        for w in a["warnings"]:
            if f"- {w}" not in warnings:
                warnings.append(f"- {w}")

    title = run.get("story_title") or ""
    lines = [
        f"# Pipeline record — {run['story_id']}{': ' + title if title else ''}",
        "",
        "> Written by the factory's orchestrator (the boss) each time this story's run "
        "stops. The factory database is the live record; this is its committed snapshot. "
        "Do not hand-edit it.",
        "",
        f"- **Project:** {run.get('project_slug') or '—'}",
        f"- **Run:** #{run['id']} · **Status:** {run['status']} · "
        f"**Stage:** {run.get('current_stage') or '—'}",
        f"- **Started:** {_when(run.get('started_at'))} UTC · "
        f"**Stopped:** {_when(run.get('finished_at'))} UTC",
        f"- **Story spend:** ~${spend.estimated_usd:.2f} of "
        f"${settings().budget.max_story_cost_usd:.2f} "
        "(estimated at API list prices"
        + (f"; {spend.unknown_calls} call(s) with unknown usage" if spend.unknown_calls else "")
        + ")",
        "",
        "## Next authorized step",
        "",
        _next_step(run, pending),
        "",
        "## Blockers",
        "",
        *(blockers or ["- none"]),
        "",
        "## Trail",
        "",
        "Every boss authorization (`boss`), agent verdict (`agent`) and gate verdict "
        "(`gate`), in the order they happened.",
        "",
        "| When (UTC) | Kind | Step | Outcome |",
        "|---|---|---|---|",
        *(_trail_row(e) for e in events),
        "",
        "## Warnings (recorded, not blocking)",
        "",
        *(warnings or ["- none"]),
        "",
    ]
    return "\n".join(lines)


def write_pipeline_record(db_path: Path, run_id: int, project_dir: Path) -> Path | None:
    """Write `docs/work/<story>/PIPELINE.md` for `run_id`; None if there is no such run.

    The caller commits it (it is project evidence under `docs/work/`).
    """
    text = render_pipeline_record(db_path, run_id)
    if text is None:
        return None
    with get_db(db_path) as conn:
        story_id = get_run(conn, run_id)["story_id"]  # type: ignore[index]
    target = work_dir_for(project_dir, story_id) / FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
