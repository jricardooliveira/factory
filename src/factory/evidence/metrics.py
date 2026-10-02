"""SDLC indicators over the factory's own history — and an honest list of what
cannot be measured yet.

The AI-Native SDLC playbook ends with a leading/lagging indicator per stage. Most
of them are one SQL query away here: every verdict, gate outcome, retry, cost and
model is already recorded per run in `agent_logs` / `gate_results`. What was
missing was any consumer — nothing in the codebase aggregated across runs, so the
factory had rich per-run telemetry and no readable memory of its own behaviour.

**Why `not_measurable` is a first-class output.** The temptation is to print
`$0.00` for cost and move on. But `agent_logs.cost_usd` is NULL in every row ever
written (opencode's usage events are not being harvested by
`adapters.opencode._extract_usage_from_json_stream`), which means the `$1` per-task
remediation budget in `gates.py` has never once bound — `get_run_cost` returns 0.0
and the comparison is always true. A dashboard reading "$0.00 spent" would present
a broken instrument as a healthy reading. Naming the gap in the tool itself is the
difference between a metric and a decoration.

Pure and TUI-free (like `interfaces/board/data.py`): `compute()` returns data, `render_*`
formats it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from factory.state.db import get_db


@dataclass
class FactoryMetrics:
    # ── Throughput (Stage 1-3 lagging) ────────────────────────────
    total_runs: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    completion_rate: float = 0.0

    # ── First-pass quality (Stage 3 leading, Stage 4 leading) ─────
    first_pass_rate: float = 0.0
    total_coder_retries: int = 0
    gate_pass_rate: dict[str, float] = field(default_factory=dict)
    gate_counts: dict[str, int] = field(default_factory=dict)

    # ── Trust per interruption (this factory's own north star) ────
    checkpoints_reached: int = 0
    checkpoints_answered: int = 0
    checkpoints_pending: int = 0
    runs_per_checkpoint: float = 0.0

    # ── Cost (Stage 3-5, and the gates.py budget) ─────────────────
    cost_measurable: bool = False
    cost_coverage: float = 0.0
    total_cost_usd: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0

    # ── What this factory cannot answer yet, and why ──────────────
    not_measurable: list[str] = field(default_factory=list)


def _rate(numerator: float, denominator: float) -> float:
    return (numerator / denominator) if denominator else 0.0


def compute(db_path: Path) -> FactoryMetrics:
    """Aggregate the factory's history. Read-only; safe to run any time."""
    m = FactoryMetrics()
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) n FROM pipeline_runs GROUP BY status"
        ).fetchall()
        m.by_status = {r["status"]: r["n"] for r in rows}
        m.total_runs = sum(m.by_status.values())
        m.completion_rate = _rate(m.by_status.get("completed", 0), m.total_runs)

        # ── Gate pass rate per gate ───────────────────────────────
        for r in conn.execute(
            "SELECT gate_name, COUNT(*) n, SUM(passed) p FROM gate_results GROUP BY gate_name"
        ).fetchall():
            m.gate_counts[r["gate_name"]] = r["n"]
            m.gate_pass_rate[r["gate_name"]] = _rate(r["p"] or 0, r["n"])

        # ── First-pass rate: a run where no coder task was attempted twice.
        # One row per (run, task) is a clean first pass; extras are retries.
        attempts = conn.execute(
            "SELECT run_id, stage_type, COUNT(*) n FROM agent_logs "
            "WHERE agent = 'coder-agent' GROUP BY run_id, stage_type"
        ).fetchall()
        runs_with_coder = {a["run_id"] for a in attempts}
        retried_runs = {a["run_id"] for a in attempts if a["n"] > 1}
        m.total_coder_retries = sum(max(0, a["n"] - 1) for a in attempts)
        m.first_pass_rate = _rate(
            len(runs_with_coder - retried_runs), len(runs_with_coder)
        )

        # ── Checkpoints: how often the operator was interrupted ───
        cp = conn.execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN human_response IS NULL THEN 1 ELSE 0 END) pending "
            "FROM gate_results WHERE needs_human = 1"
        ).fetchone()
        m.checkpoints_reached = cp["n"] or 0
        m.checkpoints_pending = cp["pending"] or 0
        m.checkpoints_answered = m.checkpoints_reached - m.checkpoints_pending
        m.runs_per_checkpoint = _rate(m.total_runs, m.checkpoints_reached)

        # ── Cost, and whether it is real ──────────────────────────
        usage = conn.execute(
            "SELECT COUNT(*) n, COUNT(cost_usd) with_cost, "
            "COALESCE(SUM(cost_usd),0) cost, COALESCE(SUM(tokens_in),0) ti, "
            "COALESCE(SUM(tokens_out),0) to_ FROM agent_logs"
        ).fetchone()
        total_calls = usage["n"] or 0
        m.cost_coverage = _rate(usage["with_cost"] or 0, total_calls)
        m.cost_measurable = (usage["with_cost"] or 0) > 0
        m.total_cost_usd = round(float(usage["cost"]), 6)
        m.total_tokens_in = int(usage["ti"])
        m.total_tokens_out = int(usage["to_"])

    if not m.cost_measurable:
        m.not_measurable.append(
            f"cost / tokens — 0 of {total_calls} agent calls recorded a cost. "
            "opencode's usage events are not being harvested "
            "(opencode_client._extract_usage_from_json_stream), so the $"
            f"{'%.2f' % 1.0} per-task remediation budget in gates.py has never bound: "
            "get_run_cost() always returns 0.0 and the comparison is always true. "
            "Only MAX_CODER_ATTEMPTS is actually limiting the loop."
        )
    m.not_measurable.append(
        "time-to-first-review / review latency — gate_results.responded_at was only "
        "added recently, so runs predating it carry no answer time."
    )
    m.not_measurable.append(
        "defects caught pre-merge vs. in production — nothing deploys, so there is "
        "no post-production signal to compare against (see EFFECTIVENESS.md §9)."
    )
    return m


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def render_markdown(m: FactoryMetrics) -> str:
    lines = [
        "# Factory Metrics",
        "",
        f"**{m.total_runs} runs** · {_pct(m.completion_rate)} completed.",
        "",
        "## Throughput",
        "",
        "| Status | Runs |",
        "|---|---:|",
    ]
    for status, n in sorted(m.by_status.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {status} | {n} |")

    lines += [
        "",
        "## First-pass quality",
        "",
        f"- First-pass rate (no coder retry): **{_pct(m.first_pass_rate)}**",
        f"- Total coder retries: **{m.total_coder_retries}**",
        "",
        "| Gate | Runs | Pass rate |",
        "|---|---:|---:|",
    ]
    for gate in sorted(m.gate_pass_rate):
        lines.append(
            f"| `{gate}` | {m.gate_counts.get(gate, 0)} | {_pct(m.gate_pass_rate[gate])} |"
        )

    lines += [
        "",
        "## Trust per interruption",
        "",
        f"- Checkpoints reached: **{m.checkpoints_reached}** "
        f"({m.checkpoints_answered} answered, {m.checkpoints_pending} pending)",
        f"- Runs per interruption: **{m.runs_per_checkpoint:.1f}**",
        "",
        "## Cost",
        "",
    ]
    if m.cost_measurable:
        lines += [
            f"- Total: **${m.total_cost_usd:.4f}** "
            f"({m.total_tokens_in:,} in / {m.total_tokens_out:,} out tokens)",
            f"- Coverage: {_pct(m.cost_coverage)} of agent calls recorded a cost",
        ]
    else:
        lines.append("- **Not measurable** — see below.")

    lines += ["", "## NOT MEASURABLE", "",
              "Indicators this factory cannot honestly report yet:", ""]
    lines += [f"- {n}" for n in m.not_measurable]
    return "\n".join(lines) + "\n"
