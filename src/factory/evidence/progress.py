"""Per-run progress, derived purely from the stored record.

Where a run is in the pipeline (`run_pipeline_progress`) and what happened in it,
in order (`run_timeline`), computed from `agent_logs` + `gate_results` alone, plus
the one-line markup renderings every surface shares. It is evidence of a run, not
a view of one: the CLI review, the Textual board AND the offline scenario matrix
(`selftest.simulate`) all read it, so it must sit below `interfaces` — and below
`runs` too, since `selftest` may not import the run service.

The `render_*` helpers return rich-markup *strings* (no rich import): a caller
that wants plain text strips the tags, as `selftest.simulate` does.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from factory.state.db import get_db, get_run_gates, get_run_logs


@dataclass
class StageStatus:
    label: str
    status: str  # done | failed | waiting | current | pending
    detail: str = ""


# Canonical pipeline order. Gate stages use their gate_results name; agent stages
# use the agent name. "release" isn't built yet (always pending).
_PIPELINE = [
    ("spec-agent", "Spec", "agent"),
    ("gate-1-spec", "Gate 1", "gate"),
    ("architect-agent", "Architect", "agent"),
    ("gate-2-architect", "Gate 2", "gate"),
    ("coder-agent", "Coder", "agent"),
    ("gate-build", "Build", "gate"),
    ("tester-agent", "Tester", "agent"),
    ("gate-test", "Test", "gate"),
    ("release", "Release", "release"),
]
_DONE_VERDICTS = {"pass", "warn", "complete"}  # warn = passed-with-warnings, stage done
_FAIL_VERDICTS = {"blocked", "error", "fail"}


def run_pipeline_progress(db_path: Path, run_id: int) -> list[StageStatus]:
    """Per-stage status for one run, derived from its agent logs + gate results."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT status, current_stage FROM pipeline_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not row:
            return []
        status, current = row["status"], (row["current_stage"] or "")
        logs = get_run_logs(conn, run_id)
        gates = get_run_gates(conn, run_id)

    by_agent: dict[str, list[dict]] = {}
    for log in logs:
        by_agent.setdefault(log["agent"], []).append(log)
    gate_by_name: dict[str, dict] = {g["gate_name"]: g for g in gates}  # latest wins
    running = status == "running"

    out: list[StageStatus] = []
    for key, label, kind in _PIPELINE:
        detail = ""
        if kind == "release":
            st = "pending"  # release-agent not built yet
        elif kind == "agent":
            ls = by_agent.get(key, [])
            verdicts = [(x.get("verdict") or "") for x in ls]
            if key == "coder-agent" and ls:
                slots = {x.get("stage_type") or "?": x.get("verdict") for x in ls}
                done = sum(1 for v in slots.values() if v == "complete")
                detail = f"{done}/{len(slots)} tasks"
            if running and current == key:
                st = "current"
            elif not ls:
                st = "pending"
            elif any(v in _FAIL_VERDICTS for v in verdicts):
                st = "failed"
            elif any(v in _DONE_VERDICTS for v in verdicts):
                st = "done"
            else:
                st = "pending"
        else:  # gate
            g = gate_by_name.get(key)
            if running and current == key:
                st = "current"
            elif g is None:
                st = "pending"
            elif g.get("needs_human") and status == "waiting_human" and not g.get("human_response"):
                st = "waiting"
            elif g.get("passed"):
                st = "done"
            else:
                st = "failed"
        out.append(StageStatus(label, st, detail))
    return out


_STAGE_ICON = {
    "done": ("✓", "green"),
    "failed": ("✗", "red"),
    "waiting": ("⏸", "yellow"),
    "current": ("▶", "cyan"),
    "pending": ("○", "dim"),
}


def render_flow(stages: list[StageStatus]) -> str:
    """One-line agent pipeline flow with per-stage status icons (rich markup)."""
    if not stages:
        return ""
    parts = []
    for s in stages:
        icon, style = _STAGE_ICON.get(s.status, ("○", "dim"))
        label = s.label + (f" {s.detail}" if s.detail else "")
        parts.append(f"[{style}]{icon} {label}[/{style}]")
    return "  [dim]→[/dim]  ".join(parts)


@dataclass
class TimelineEvent:
    when: str  # ISO timestamp
    kind: str  # "run" | "agent" | "gate"
    label: str
    detail: str = ""
    status: str = "info"  # done | failed | waiting | info — for styling


def _hms(iso: str) -> str:
    return iso[11:19] if iso and len(iso) >= 19 else (iso or "")


def run_timeline(db_path: Path, run_id: int) -> list[TimelineEvent]:
    """Chronological story of a run: start, each agent, each gate, finish."""
    with get_db(db_path) as conn:
        run = conn.execute(
            "SELECT status, started_at, finished_at, error FROM pipeline_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if not run:
            return []
        run = dict(run)
        logs = get_run_logs(conn, run_id)
        gates = get_run_gates(conn, run_id)

    events: list[TimelineEvent] = []
    if run["started_at"]:
        events.append(TimelineEvent(run["started_at"], "run", "run started"))

    for log in logs:
        verdict = (log.get("verdict") or "").lower()
        status = "failed" if verdict in ("blocked", "error", "fail") else "done"
        bits = [verdict or "?"]
        if log.get("stage_type") and log["stage_type"] not in ("agent", None):
            bits.append(log["stage_type"])
        if log.get("duration_secs"):
            bits.append(f"{log['duration_secs']:.1f}s")
        if log.get("cost_usd"):
            bits.append(f"${log['cost_usd']:.4f}")
        events.append(TimelineEvent(log["created_at"], "agent", log["agent"], " · ".join(bits), status))

    for g in gates:
        if g.get("needs_human") and not g.get("human_response"):
            status, detail = "waiting", "awaiting human sign-off"
        elif g.get("human_response"):
            status = "done" if g["human_response"].startswith("APPROVED") else "failed"
            detail = g["human_response"]
        else:
            status = "done" if g["passed"] else "failed"
            detail = g.get("reason") or ""
        events.append(TimelineEvent(g["checked_at"], "gate", g["gate_name"], detail, status))

    if run["finished_at"]:
        st = run["status"]
        status = "done" if st == "completed" else ("waiting" if st == "waiting_human" else "failed")
        events.append(TimelineEvent(run["finished_at"], "run", f"run {st}", run.get("error") or "", status))

    events.sort(key=lambda e: e.when)
    return events


_TIMELINE_ICON = {"done": ("✓", "green"), "failed": ("✗", "red"),
                  "waiting": ("⏸", "yellow"), "info": ("•", "cyan")}


def render_timeline(events: list[TimelineEvent]) -> str:
    """Render a timeline as multi-line rich markup."""
    lines = []
    for e in events:
        icon, style = _TIMELINE_ICON.get(e.status, ("•", "dim"))
        detail = f"  [dim]{e.detail}[/dim]" if e.detail else ""
        lines.append(f"[dim]{_hms(e.when)}[/dim] [{style}]{icon}[/{style}] {e.label}{detail}")
    return "\n".join(lines)
