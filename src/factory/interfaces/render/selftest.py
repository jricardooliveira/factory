"""Self-test output: tiers, the scenario matrix, evals, doctor and metrics."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from factory.interfaces.render import output


_COGNITIVE_WORK = {
    "spec-agent": "planning / decomposition",
    "architect-agent": "design trade-offs, risk, ADRs",
    "tester-agent": "risk + security / QA review",
    "coder-agent": "implementation",
}
_TIER_COLOR = {"frontier": "magenta", "standard": "cyan", "fast": "green"}


def print_tiers(
    agent_tiers: dict[str, str],
    escalate_on_retry: dict[str, str],
    model_for_tier: Callable[[str], str],
) -> None:
    """Which model each agent runs at — the factory's leverage allocation."""
    table = Table(title="Model tiers — frontier reserved for high-leverage thinking")
    table.add_column("Agent", style="bold")
    table.add_column("Cognitive work")
    table.add_column("Tier")
    table.add_column("Model", style="dim")
    for agent, tier in agent_tiers.items():
        color = _TIER_COLOR.get(tier, "white")
        escalates = agent in escalate_on_retry
        tier_label = f"[{color}]{tier}[/{color}]"
        model = model_for_tier(tier)
        if escalates:
            esc_tier = escalate_on_retry[agent]
            tier_label += f" [dim]→ {esc_tier} on retry[/dim]"
            model += f"  [dim](retry: {model_for_tier(esc_tier)})[/dim]"
        table.add_row(agent, _COGNITIVE_WORK.get(agent, ""), tier_label, model)
    output.console.print(table)
    output.console.print(
        "[dim]Override a tier without editing code: "
        "FACTORY_TIER_FRONTIER=provider/model factory run ...[/dim]"
    )



def print_simulation(results: Iterable[Any]) -> None:
    """The offline scenario matrix (`selftest.simulate.ScenarioResult`s)."""
    results = list(results)
    table = Table(title="🧪 Factory Simulation (offline, no tokens)", border_style="cyan")
    table.add_column("Scenario", style="bold")
    table.add_column("Expected")
    table.add_column("Actual")
    table.add_column("OK", justify="center")
    table.add_column("Flow", style="dim")
    for r in results:
        ok = "[green]✅[/green]" if r.passed else "[red]❌[/red]"
        actual = r.actual_status + (f" ({r.error})" if r.error else "")
        table.add_row(r.scenario.name, r.scenario.expected_status, actual, ok, r.flow_text[:60])
    output.console.print(table)
    passed = sum(1 for r in results if r.passed)
    style = "green" if passed == len(results) else "red"
    output.console.print(f"  [{style}]{passed}/{len(results)} scenarios behaving as expected[/{style}]")


def print_eval_captured(run_id: int, path: Path) -> None:
    output.console.print(f"[green]Captured run #{run_id} as a permanent eval case:[/green] {path}")


def print_eval_replayed(result: Any) -> None:
    """Whether a just-captured eval case reproduces its recorded outcome."""
    icon = "[green]✅[/green]" if result.passed else "[red]❌[/red]"
    output.console.print(f"  {icon} replays to '{result.actual}' (expected '{result.expected}')")
    if not result.passed:
        output.console.print(f"  [red]{result.detail}[/red]")


def print_evals(report: Any, threshold: float) -> None:
    """The agent-configuration eval report (`selftest.evals.EvalReport`)."""
    table = Table(
        title="🔬 Agent-Configuration Evals (offline, no tokens)", border_style="cyan"
    )
    table.add_column("Check", style="bold", overflow="fold")
    table.add_column("Kind")
    table.add_column("OK", justify="center")
    table.add_column("Detail", style="dim", overflow="fold")
    for r in report.results:
        icon = "[green]✅[/green]" if r.passed else "[red]❌[/red]"
        table.add_row(r.name, r.kind, icon, (r.detail or "")[:90])
    output.console.print(table)

    passed = len(report.results) - len(report.failures)
    style = "green" if report.passed else "red"
    output.console.print(
        f"  [{style}]{passed}/{len(report.results)} checks green "
        f"({round(report.pass_rate * 100)}%) — threshold "
        f"{round(threshold * 100)}%[/{style}]"
    )


_DOCTOR_ICON = {
    "ok": "[green]✅[/green]",
    "fail": "[red]❌[/red]",
    "warn": "[yellow]⚠️[/yellow]",
    "skip": "[dim]–[/dim]",
}


def print_doctor(report: Any) -> None:
    """The preflight report (`selftest.doctor.DoctorReport`), one line per check."""
    output.console.print("[bold]factory doctor[/bold] — preflight before a run spends tokens")
    for c in report.checks:
        icon = _DOCTOR_ICON.get(c.status, c.status)
        output.console.print(f"  {icon} [bold]{c.name}[/bold]  [dim]{c.detail}[/dim]")
    if report.passed:
        output.console.print("  [green]Ready: every blocking check passed.[/green]")
    else:
        output.console.print(
            "  [red]Not ready: fix the ❌ lines above before `factory run` "
            "(re-point a tier with FACTORY_TIER_<TIER>=provider/model or edit "
            "agents/tiers.toml; point FACTORY_HOME at a writable directory).[/red]"
        )


def print_metrics(m: Any, db_path: Path) -> None:
    """The playbook's SDLC indicators (`evidence.metrics.Metrics`), NOT MEASURABLE last."""
    output.console.print()
    output.console.print(Rule(f"[bold blue]Factory Metrics[/bold blue]  [dim]{db_path}[/dim]",
                       style="blue"))

    throughput = Table(title="Throughput", border_style="cyan")
    throughput.add_column("Status", style="bold")
    throughput.add_column("Runs", justify="right")
    for status, n in sorted(m.by_status.items(), key=lambda kv: -kv[1]):
        throughput.add_row(status, str(n))
    throughput.add_row("[bold]total[/bold]", f"[bold]{m.total_runs}[/bold]")
    output.console.print(throughput)
    output.console.print(f"  Completion rate: [bold]{round(m.completion_rate * 100)}%[/bold]\n")

    quality = Table(title="First-pass quality", border_style="cyan")
    quality.add_column("Gate", style="bold")
    quality.add_column("Runs", justify="right")
    quality.add_column("Pass rate", justify="right")
    for gate in sorted(m.gate_pass_rate):
        rate = m.gate_pass_rate[gate]
        colour = "green" if rate >= 0.8 else ("yellow" if rate >= 0.5 else "red")
        quality.add_row(gate, str(m.gate_counts.get(gate, 0)),
                        f"[{colour}]{round(rate * 100)}%[/{colour}]")
    output.console.print(quality)
    output.console.print(
        f"  First-pass rate (no coder retry): [bold]{round(m.first_pass_rate * 100)}%[/bold]"
        f"   ·   coder retries: [bold]{m.total_coder_retries}[/bold]\n"
    )

    output.console.print(
        f"  [bold]Trust per interruption[/bold]: {m.checkpoints_reached} checkpoint(s) "
        f"({m.checkpoints_answered} answered, {m.checkpoints_pending} pending) "
        f"— {m.runs_per_checkpoint:.1f} runs per interruption\n"
    )

    if m.cost_measurable:
        output.console.print(
            f"  [bold]Cost[/bold]: ${m.total_cost_usd:.4f} "
            f"({m.total_tokens_in:,} in / {m.total_tokens_out:,} out tokens, "
            f"{round(m.cost_coverage * 100)}% of calls instrumented)\n"
        )

    output.console.print(Panel(
        "\n".join(f"• {n}" for n in m.not_measurable),
        title="⚠️  NOT MEASURABLE", border_style="yellow",
    ))
