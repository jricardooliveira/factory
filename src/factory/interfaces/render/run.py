"""Live output of a run: the per-node lines `factory run/resume/retry/replay` print."""

from __future__ import annotations

from typing import Any
import re

from pydantic import ValidationError
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from factory.domain.contracts import ArchitectOutput, CoderOutput, SpecOutput
from factory.interfaces.render import output
from factory.runs import ResumeEntered, RetryStarted, RunOutcome, RunStarted


def print_header(request: str) -> None:
    output.console.print()
    output.console.print(Rule("[bold blue]Factory Pipeline[/bold blue]", style="blue"))
    output.console.print(Panel(request, title="📋 Request", border_style="cyan"))
    output.console.print()


def print_agent_start(agent: str) -> None:
    output.console.print(f"  🤖 [bold cyan]{agent}[/bold cyan] running...", end="")


def print_agent_done(agent: str, verdict: str, duration: float) -> None:
    style = output.verdict_style(verdict)
    output.console.print(
        f"\r  🤖 [bold cyan]{agent}[/bold cyan] → [{style}]{verdict.upper()}[/{style}] ({duration:.1f}s)"
    )


def print_gate(gate_name: str, passed: bool, reason: str) -> None:
    icon = output.gate_icon(passed)
    style = "green" if passed else "red"
    output.console.print(f"  {icon} [bold]{gate_name}[/bold]: [{style}]{reason}[/{style}]")
    output.console.print()


def print_spec_summary(spec: dict) -> None:
    # A blocked/off-script spec may omit required fields; never let display crash.
    try:
        s = SpecOutput.model_validate(spec)
    except ValidationError:
        verdict = spec.get("verdict", "unknown")
        questions = spec.get("questions") or []
        output.console.print(f"  [dim]No story produced (verdict: {verdict}).[/dim]")
        for q in questions:
            output.console.print(f"    [yellow]?[/yellow] {q}")
        output.console.print()
        return
    table = Table(title="📝 Story Definition", show_header=False, border_style="cyan", padding=(0, 2))
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("ID", s.story_id)
    table.add_row("Title", s.title)
    table.add_row("Type", s.type)
    table.add_row("Problem", s.problem)
    table.add_row("AC", "\n".join(f"• {ac}" for ac in s.acceptance_criteria))
    table.add_row("Tasks", "\n".join(f"• {t.id}: {t.title}" for t in s.tasks))
    output.console.print(table)
    output.console.print()


def print_architect_summary(arch: dict) -> None:
    a = ArchitectOutput.model_validate(arch)
    table = Table(title="🏗️  Architecture", show_header=False, border_style="yellow", padding=(0, 2))
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Notes", a.architecture_notes[:300])
    table.add_row("Modules", ", ".join(a.modules_affected))
    table.add_row("Constraints", "\n".join(f"• {c}" for c in a.implementation_constraints))
    table.add_row("DB Impact", a.db_impact)
    table.add_row("API Impact", a.api_impact)
    if a.risks:
        table.add_row("Risks", "\n".join(f"⚠ {r}" for r in a.risks))
    output.console.print(table)
    output.console.print()


def print_coder_summary(coder: dict) -> None:
    c = CoderOutput.model_validate(coder)
    table = Table(title="💻 Implementation", show_header=False, border_style="green", padding=(0, 2))
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Summary", c.implementation_summary)
    table.add_row("Files Created", "\n".join(c.files_created) or "none")
    table.add_row("Tests Added", "\n".join(c.tests_added) or "none")
    table.add_row(
        "Coverage",
        f"Happy: {'✅' if c.test_coverage.happy_path else '❌'}  "
        f"Edge: {'✅' if c.test_coverage.edge_case else '❌'}  "
        f"Error: {'✅' if c.test_coverage.error_handling else '❌'}",
    )
    if c.follow_ups:
        table.add_row("Follow-ups", "\n".join(f"• {f}" for f in c.follow_ups))
    output.console.print(table)
    output.console.print()


def print_run_started(event: RunStarted) -> None:
    """Header, replay/spec context and the run id — what a fresh run shows first."""
    print_header(event.request)
    if event.replay_of:
        output.console.print(f"  [yellow]↻ Replaying frozen outputs from run #{event.replay_of} (no opencode calls)[/yellow]\n")
    if event.project_spec_text:
        output.console.print(Panel(event.project_spec_text, title="📐 Project Spec", border_style="magenta"))
        output.console.print()
    output.console.print(f"  📦 Run [bold]#{event.run_id}[/bold] | Story [bold]{event.story_id}[/bold]\n")


def print_run_node(node_name: str, node_output: dict[str, Any]) -> None:
    """One node of a fresh or replayed run, as it completes."""
    if node_name == "spec-agent":
        if node_output.get("status") in ("failed", "blocked"):
            print_agent_done("spec-agent", node_output.get("status", "error"), 0)
        else:
            spec = node_output.get("spec", {})
            v = spec.get("verdict", "unknown")
            print_agent_done("spec-agent", v, 0)
            print_spec_summary(spec)
            for label, key in (("INTENT", "intent_path"), ("SPEC", "spec_path")):
                if node_output.get(key):
                    output.console.print(f"    [dim]📝 {label}: {node_output[key]}[/dim]")

    elif node_name == "gate-1":
        g = node_output.get("gate_1", {})
        print_gate("Gate 1 (Spec)", g.get("passed", False), g.get("reason", ""))

    elif node_name == "architect-agent":
        if node_output.get("status") == "failed":
            print_agent_done("architect-agent", "error", 0)
        else:
            arch = node_output.get("architect", {})
            v = arch.get("verdict", "unknown")
            print_agent_done("architect-agent", v, 0)
            print_architect_summary(arch)
            if node_output.get("adr_path"):
                output.console.print(f"    [dim]📝 ADR: {node_output['adr_path']}[/dim]")
            if node_output.get("plan_path"):
                output.console.print(f"    [dim]📝 PLAN: {node_output['plan_path']}[/dim]")

    elif node_name == "gate-2":
        g = node_output.get("gate_2", {})
        print_gate("Gate 2 (Architecture)", g.get("passed", False), g.get("reason", ""))

    elif node_name == "coder-agent":
        coder = node_output.get("coder", {})
        if coder:
            v = coder.get("verdict", "unknown")
            print_agent_done("coder-agent", v, 0)
            print_coder_summary(coder)
        elif node_output.get("status") == "failed":
            print_agent_done("coder-agent", "error", 0)
        gb = node_output.get("gate_build")
        if gb:
            print_gate("Gate Build (Verify)", gb.get("passed", False), gb.get("reason", ""))


def print_resume_node(node_name: str, node_output: dict[str, Any]) -> None:
    """One node of a resumed run.

    Deliberately terser than `print_run_node` (no architect summary, no line for a
    coder that failed without output) — that is how resumes have always rendered.
    """
    if node_name == "architect-agent":
        arch = node_output.get("architect", {})
        print_agent_done("architect-agent", arch.get("verdict", "unknown"), 0)
        if node_output.get("adr_path"):
            output.console.print(f"    [dim]📝 ADR: {node_output['adr_path']}[/dim]")
    elif node_name == "gate-2":
        g = node_output.get("gate_2", {})
        print_gate("Gate 2 (Architecture)", g.get("passed", False), g.get("reason", ""))
    elif node_name == "coder-agent":
        coder = node_output.get("coder", {})
        if coder:
            print_agent_done("coder-agent", coder.get("verdict", "unknown"), 0)
            print_coder_summary(coder)
        gb = node_output.get("gate_build")
        if gb:
            print_gate("Gate Build (Verify)", gb.get("passed", False), gb.get("reason", ""))


def print_retry_started(event: RetryStarted) -> None:
    output.console.print(Panel(
        f"Retrying run #{event.run_id} from your recorded decision at "
        f"[bold]{event.gate_name}[/bold]:\n\n[bold]{event.action.upper()}[/bold]: {event.feedback}",
        title="↻ Retry", border_style="cyan",
    ))


def print_resume_entered(event: ResumeEntered) -> None:
    run_id, action, decision = event.run_id, event.action, event.decision
    if event.entry == "spec":
        output.console.print(Panel(
            f"Run #{run_id} [bold yellow]REJECTED at Checkpoint 1[/bold yellow] — "
            f"re-specifying the story with your answers...\n\n{decision}",
            border_style="yellow",
        ))
    elif event.entry == "architect":
        verb = "APPROVED" if action == "approve" else "REJECTED"
        colour = "green" if action == "approve" else "yellow"
        output.console.print(Panel(
            f"Run #{run_id} [bold {colour}]{verb}[/bold {colour}] — "
            f"{'designing the approved story' if action == 'approve' else 'redesigning with your feedback'}"
            f"...\n\n{decision}",
            border_style=colour,
        ))
    else:
        output.console.print(Panel(
            f"Run #{run_id} [bold green]APPROVED[/bold green] — continuing to coder-agent...",
            border_style="green",
        ))


def print_final_status(status: str, error: str | None = None, human_questions: list[str] | None = None) -> None:
    output.console.print()
    if status == "completed":
        output.console.print(Rule("[bold green]✅ Pipeline Completed[/bold green]", style="green"))
    elif status == "waiting_human":
        output.console.print(Rule("[bold yellow]⏸️  Pipeline Paused — Human Approval Required[/bold yellow]", style="yellow"))
        if human_questions:
            for q in human_questions:
                output.console.print(Panel(q, border_style="yellow"))
        output.console.print("\n  [dim]To continue:[/dim]")
        output.console.print("    factory [bold cyan]approve <run_id>[/bold cyan]    Accept and continue to coder")
        output.console.print("    factory [bold cyan]reject <run_id>[/bold cyan]     Reject and stop the pipeline")
    elif status == "blocked":
        output.console.print(Rule("[bold yellow]⏸️  Pipeline Blocked[/bold yellow]", style="yellow"))
        if error:
            output.console.print(Panel(error, title="Blocked — Agent needs input", border_style="yellow"))
    else:
        output.console.print(Rule("[bold red]❌ Pipeline Failed[/bold red]", style="red"))
        if error:
            output.console.print(Panel(error, title="Error", border_style="red"))


def print_review_table(logs: list[dict], gates: list[dict]) -> None:
    """Print a review table of all agent logs and gates for a run."""
    output.console.print()
    output.console.print(Rule("[bold]📊 Pipeline Review[/bold]"))

    if logs:
        table = Table(title="Agent Logs", border_style="dim")
        table.add_column("Agent", style="cyan")
        table.add_column("Verdict", justify="center")
        table.add_column("Duration", justify="right")
        for log in logs:
            v = log.get("verdict") or "—"
            style = output.verdict_style(v)
            dur = f"{log['duration_secs']:.1f}s" if log.get("duration_secs") else "—"
            table.add_row(log["agent"], Text(v.upper(), style=style), dur)
        output.console.print(table)

    if gates:
        table = Table(title="Gate Results", border_style="dim")
        table.add_column("Gate", style="cyan")
        table.add_column("Result", justify="center")
        table.add_column("Reason")
        for gate in gates:
            label, style = gate_result_label(gate)
            table.add_row(gate["gate_name"], Text(label, style=style), gate["reason"])
        output.console.print(table)
    output.console.print()


def gate_result_label(gate: dict) -> tuple[str, str]:
    """(label, style) for one gate row. A gate that did not pass but parked for the
    operator — gate-release when evidence is missing — is waiting, not failed."""
    if gate["passed"]:
        return "PASS", "green"
    if gate.get("needs_human"):
        return "NOT READY", "yellow"
    return "FAIL", "red"


def print_run_finished(outcome: RunOutcome, logs: list[dict], gates: list[dict]) -> None:
    print_final_status(outcome.status, outcome.error, human_questions=outcome.human_questions)
    print_review_table(logs, gates)
