"""Commands that measure the factory itself: simulate, evals, metrics, tiers.

All offline and zero-token. `evals` exits non-zero below its pass threshold so
`make evals` / CI can gate a merge on it.
"""

from __future__ import annotations

import sys

from factory.interfaces import render
from factory.interfaces.cli.common import DB_PATH, fail, report_path


def tiers_command(args: list[str]) -> None:
    """Which model each agent runs at — the factory's leverage allocation."""
    from factory.agent_config import tiers as mt

    render.print_tiers(mt.AGENT_TIERS, mt.ESCALATE_ON_RETRY, mt.model_for_tier)


def simulate_command(args: list[str]) -> None:
    """Run the offline scenario matrix (deterministic, no tokens) + report."""
    from factory.selftest.simulate import render_markdown, simulate_all

    results = simulate_all()
    render.print_simulation(results)

    path = report_path(args, "simulation-report.md")
    if path is not None:
        path.write_text(render_markdown(results), encoding="utf-8")
        render.print_report_written(path)


def evals_command(args: list[str]) -> None:
    """Regression-test the AGENT CONFIGURATION (offline, no tokens).

    The playbook's Stage-4 continuous-evals play: the factory IS an agent
    configuration, so a change to an agent definition, the gate policy or the
    tier policy is a behaviour change and needs a regression net. Exits non-zero
    below the pass threshold so `make evals` / CI can gate a merge on it.
    """
    from factory.selftest import evals as ev

    if args and args[0] == "capture":
        _capture(ev, args)
        return

    report = ev.run_all()
    render.print_evals(report, ev.PASS_THRESHOLD)

    path = report_path(args, "eval-report.md")
    if path is not None:
        path.write_text(ev.render_markdown(report), encoding="utf-8")
        render.print_report_written(path)

    if not report.passed:
        sys.exit(1)


def _capture(ev, args: list[str]) -> None:
    """`factory evals capture <run_id> <name> ["description"]`."""
    if len(args) < 3:
        fail('Usage: factory evals capture <run_id> <name> ["description"]')
    try:
        run_id = int(args[1])
    except ValueError:
        fail(f"Run id must be a number, got '{args[1]}'")
    name = args[2]
    description = " ".join(args[3:]).strip()
    try:
        path = ev.capture_case(DB_PATH, run_id, name, description=description)
    except ValueError as exc:
        fail(str(exc))
    render.print_eval_captured(run_id, path)
    # Prove the new case actually replays — a captured case that doesn't
    # reproduce is worse than no case at all.
    result = ev.run_case(next(c for c in ev.load_cases() if c.name == name))
    render.print_eval_replayed(result)
    if not result.passed:
        sys.exit(1)


def metrics_command(args: list[str]) -> None:
    """The playbook's SDLC indicators over this factory's own history.

    Deliberately reports what it CANNOT measure as prominently as what it can —
    a cost of $0.00 read off 47 NULL rows would present a broken instrument as a
    healthy reading.
    """
    from factory.evidence import metrics as mx
    from factory.state.db import init_db

    init_db(DB_PATH)
    m = mx.compute(DB_PATH)
    render.print_metrics(m, DB_PATH)

    path = report_path(args, "metrics.md")
    if path is not None:
        path.write_text(mx.render_markdown(m), encoding="utf-8")
        render.print_report_written(path)
