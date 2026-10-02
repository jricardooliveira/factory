"""SDLC indicators over the factory's own history — and an honest list of what
cannot be measured yet.

The AI-Native SDLC playbook ends with a leading/lagging indicator per stage. Most
of them are one SQL query away here: every verdict, gate outcome, retry, cost and
model is already recorded per run in `agent_logs` / `gate_results`. What was
missing was any consumer — nothing in the codebase aggregated across runs, so the
factory had rich per-run telemetry and no readable memory of its own behaviour.

**Why `not_measurable` is a first-class output.** The temptation is to print
`$0.00` for cost and move on. Until 2026-10-02 `agent_logs.cost_usd` was NULL in
every row (opencode reports usage on `step_finish` events, which the harvester did
not read), so the `$1` per-task budget never bound. Harvesting now works — and on a
subscription login the provider reports `$0` for real token use, so "$0.00 spent"
is still not a working budget. A dashboard that hid either would present a broken
instrument as a healthy reading; naming the gap in the tool itself is the
difference between a metric and a decoration.

Pure and TUI-free (like `interfaces/board/data.py`): `compute()` returns data, `render_*`
formats it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from factory.domain.gates import MAX_TASK_COST_USD
from factory.state import reports
from factory.state.db import get_db


@dataclass
class FactoryMetrics:
    # ── Throughput (Stage 1-3 lagging) ────────────────────────────
    total_runs: int = 0
    replay_runs: int = 0  # excluded from every outcome metric
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
        m.by_status = reports.run_status_counts(conn)
        m.total_runs = sum(m.by_status.values())
        m.replay_runs = reports.replay_run_count(conn)
        m.completion_rate = _rate(m.by_status.get("completed", 0), m.total_runs)

        # ── Gate pass rate per gate ───────────────────────────────
        for gate_name, runs, passes in reports.gate_tallies(conn):
            m.gate_counts[gate_name] = runs
            m.gate_pass_rate[gate_name] = _rate(passes, runs)

        # ── First-pass rate: a run where no coder task was attempted twice.
        # One row per (run, task) is a clean first pass; extras are retries.
        attempts = reports.coder_attempts(conn)
        runs_with_coder = {run for run, _slot, _n in attempts}
        retried_runs = {run for run, _slot, n in attempts if n > 1}
        m.total_coder_retries = sum(max(0, n - 1) for _run, _slot, n in attempts)
        m.first_pass_rate = _rate(
            len(runs_with_coder - retried_runs), len(runs_with_coder)
        )

        # ── Checkpoints: how often the operator was interrupted ───
        m.checkpoints_reached, m.checkpoints_pending = reports.checkpoint_counts(conn)
        m.checkpoints_answered = m.checkpoints_reached - m.checkpoints_pending
        m.runs_per_checkpoint = _rate(m.total_runs, m.checkpoints_reached)

        # ── Cost, and whether it is real ──────────────────────────
        usage = reports.usage_totals(conn)
        total_calls = usage["calls"]
        m.cost_coverage = _rate(usage["calls_with_cost"], total_calls)
        m.cost_measurable = usage["calls_with_cost"] > 0
        m.total_cost_usd = round(float(usage["cost_usd"]), 6)
        m.total_tokens_in = int(usage["tokens_in"])
        m.total_tokens_out = int(usage["tokens_out"])

    if not m.cost_measurable:
        m.not_measurable.append(
            f"cost / tokens — 0 of {total_calls} agent calls recorded usage. Calls made "
            "before usage harvesting was fixed (2026-10-02: opencode reports it on "
            "`step_finish` events) stored none, so for them the $"
            f"{MAX_TASK_COST_USD:.2f} per-task remediation budget in domain/gates.py never "
            "bound. Only MAX_CODER_ATTEMPTS limited those loops."
        )
    elif m.total_cost_usd == 0 and m.total_tokens_in + m.total_tokens_out > 0:
        m.not_measurable.append(
            f"currency spend — the provider reports $0 while "
            f"{m.total_tokens_in + m.total_tokens_out:,} tokens were used (a subscription "
            f"login, e.g. ChatGPT). Spend is real in tokens, not dollars, so the "
            f"${MAX_TASK_COST_USD:.2f} per-task budget cannot bind on this login: only "
            "MAX_CODER_ATTEMPTS limits the loop."
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
        f"**{m.total_runs} live runs** · {_pct(m.completion_rate)} completed"
        + (f" · {m.replay_runs} replay(s) excluded (frozen outputs, not delivered work)."
           if m.replay_runs else "."),
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
