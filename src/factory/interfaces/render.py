"""Every rich rendering helper the CLI uses — presentation only.

Functions here take data and print it; they never open the DB or drive a run.
The command modules in `interfaces/cli/` fetch (from `factory.runs`, `state`,
`evidence`, ...) and hand the result here. Output is byte-for-byte what the
single-module CLI printed before the split; keep it that way unless a change to
the operator-facing text is the point.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from factory.domain.contracts import ArchitectOutput, CoderOutput, SpecOutput
from factory.runs import ResumeEntered, RetryStarted, RunOutcome, RunStarted

console = Console()


def print_error(message: str) -> None:
    console.print(f"[red]{message}[/red]")


def _verdict_style(verdict: str) -> str:
    v = verdict.lower()
    if v in ("pass", "complete", "done", "completed"):
        return "bold green"
    elif v in ("warn", "waiting_human"):
        return "bold yellow"
    elif v in ("fail", "failed", "error", "blocked"):
        return "bold red"
    return "white"


def _gate_icon(passed: bool) -> str:
    return "✅" if passed else "❌"


# ── Live run output ───────────────────────────────────────────────


def print_header(request: str) -> None:
    console.print()
    console.print(Rule("[bold blue]Factory Pipeline[/bold blue]", style="blue"))
    console.print(Panel(request, title="📋 Request", border_style="cyan"))
    console.print()


def print_agent_start(agent: str) -> None:
    console.print(f"  🤖 [bold cyan]{agent}[/bold cyan] running...", end="")


def print_agent_done(agent: str, verdict: str, duration: float) -> None:
    style = _verdict_style(verdict)
    console.print(
        f"\r  🤖 [bold cyan]{agent}[/bold cyan] → [{style}]{verdict.upper()}[/{style}] ({duration:.1f}s)"
    )


def print_gate(gate_name: str, passed: bool, reason: str) -> None:
    icon = _gate_icon(passed)
    style = "green" if passed else "red"
    console.print(f"  {icon} [bold]{gate_name}[/bold]: [{style}]{reason}[/{style}]")
    console.print()


def print_spec_summary(spec: dict) -> None:
    # A blocked/off-script spec may omit required fields; never let display crash.
    try:
        s = SpecOutput.model_validate(spec)
    except ValidationError:
        verdict = spec.get("verdict", "unknown")
        questions = spec.get("questions") or []
        console.print(f"  [dim]No story produced (verdict: {verdict}).[/dim]")
        for q in questions:
            console.print(f"    [yellow]?[/yellow] {q}")
        console.print()
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
    console.print(table)
    console.print()


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
    console.print(table)
    console.print()


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
    console.print(table)
    console.print()


def print_run_started(event: RunStarted) -> None:
    """Header, replay/spec context and the run id — what a fresh run shows first."""
    print_header(event.request)
    if event.replay_of:
        console.print(f"  [yellow]↻ Replaying frozen outputs from run #{event.replay_of} (no opencode calls)[/yellow]\n")
    if event.project_spec_text:
        console.print(Panel(event.project_spec_text, title="📐 Project Spec", border_style="magenta"))
        console.print()
    console.print(f"  📦 Run [bold]#{event.run_id}[/bold] | Story [bold]{event.story_id}[/bold]\n")


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
                    console.print(f"    [dim]📝 {label}: {node_output[key]}[/dim]")

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
                console.print(f"    [dim]📝 ADR: {node_output['adr_path']}[/dim]")
            if node_output.get("plan_path"):
                console.print(f"    [dim]📝 PLAN: {node_output['plan_path']}[/dim]")

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
            console.print(f"    [dim]📝 ADR: {node_output['adr_path']}[/dim]")
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
    console.print(Panel(
        f"Retrying run #{event.run_id} from your recorded decision at "
        f"[bold]{event.gate_name}[/bold]:\n\n[bold]{event.action.upper()}[/bold]: {event.feedback}",
        title="↻ Retry", border_style="cyan",
    ))


def print_resume_entered(event: ResumeEntered) -> None:
    run_id, action, decision = event.run_id, event.action, event.decision
    if event.entry == "spec":
        console.print(Panel(
            f"Run #{run_id} [bold yellow]REJECTED at Checkpoint 1[/bold yellow] — "
            f"re-specifying the story with your answers...\n\n{decision}",
            border_style="yellow",
        ))
    elif event.entry == "architect":
        verb = "APPROVED" if action == "approve" else "REJECTED"
        colour = "green" if action == "approve" else "yellow"
        console.print(Panel(
            f"Run #{run_id} [bold {colour}]{verb}[/bold {colour}] — "
            f"{'designing the approved story' if action == 'approve' else 'redesigning with your feedback'}"
            f"...\n\n{decision}",
            border_style=colour,
        ))
    else:
        console.print(Panel(
            f"Run #{run_id} [bold green]APPROVED[/bold green] — continuing to coder-agent...",
            border_style="green",
        ))


def print_final_status(status: str, error: str | None = None, human_questions: list[str] | None = None) -> None:
    console.print()
    if status == "completed":
        console.print(Rule("[bold green]✅ Pipeline Completed[/bold green]", style="green"))
    elif status == "waiting_human":
        console.print(Rule("[bold yellow]⏸️  Pipeline Paused — Human Approval Required[/bold yellow]", style="yellow"))
        if human_questions:
            for q in human_questions:
                console.print(Panel(q, border_style="yellow"))
        console.print("\n  [dim]To continue:[/dim]")
        console.print("    factory [bold cyan]approve <run_id>[/bold cyan]    Accept and continue to coder")
        console.print("    factory [bold cyan]reject <run_id>[/bold cyan]     Reject and stop the pipeline")
    elif status == "blocked":
        console.print(Rule("[bold yellow]⏸️  Pipeline Blocked[/bold yellow]", style="yellow"))
        if error:
            console.print(Panel(error, title="Blocked — Agent needs input", border_style="yellow"))
    else:
        console.print(Rule("[bold red]❌ Pipeline Failed[/bold red]", style="red"))
        if error:
            console.print(Panel(error, title="Error", border_style="red"))


def print_review_table(logs: list[dict], gates: list[dict]) -> None:
    """Print a review table of all agent logs and gates for a run."""
    console.print()
    console.print(Rule("[bold]📊 Pipeline Review[/bold]"))

    if logs:
        table = Table(title="Agent Logs", border_style="dim")
        table.add_column("Agent", style="cyan")
        table.add_column("Verdict", justify="center")
        table.add_column("Duration", justify="right")
        for log in logs:
            v = log.get("verdict") or "—"
            style = _verdict_style(v)
            dur = f"{log['duration_secs']:.1f}s" if log.get("duration_secs") else "—"
            table.add_row(log["agent"], Text(v.upper(), style=style), dur)
        console.print(table)

    if gates:
        table = Table(title="Gate Results", border_style="dim")
        table.add_column("Gate", style="cyan")
        table.add_column("Result", justify="center")
        table.add_column("Reason")
        for gate in gates:
            passed = bool(gate["passed"])
            table.add_row(
                gate["gate_name"],
                Text("PASS" if passed else "FAIL", style="green" if passed else "red"),
                gate["reason"],
            )
        console.print(table)
    console.print()


def print_run_finished(outcome: RunOutcome, logs: list[dict], gates: list[dict]) -> None:
    print_final_status(outcome.status, outcome.error, human_questions=outcome.human_questions)
    print_review_table(logs, gates)


# ── factory review ────────────────────────────────────────────────


def _review_spec_output(parsed: dict) -> None:
    """Pretty-print spec-agent output for review."""
    console.print(f"    [dim]Title:[/dim]    {parsed.get('title', '—')}")
    console.print(f"    [dim]Type:[/dim]     {parsed.get('type', '—')}")
    console.print(f"    [dim]Problem:[/dim]  {parsed.get('problem', '—')}")
    ac = parsed.get("acceptance_criteria", [])
    if ac:
        console.print("    [dim]AC:[/dim]")
        for a in ac:
            console.print(f"      • {a}")
    tasks = parsed.get("tasks", [])
    if tasks:
        console.print("    [dim]Tasks:[/dim]")
        for t in tasks:
            tid = t.get("id", "?")
            title = t.get("title", "?")
            console.print(f"      • {tid}: {title}")
    questions = parsed.get("questions", [])
    if questions:
        console.print("    [dim]Open Questions:[/dim]")
        for q in questions:
            console.print(f"      ❓ {q}")


def _review_architect_output(parsed: dict) -> None:
    """Pretty-print architect-agent output for review."""
    notes = parsed.get("architecture_notes", "—")
    if len(notes) > 400:
        notes = notes[:400] + "..."
    console.print(f"    [dim]Notes:[/dim]       {notes}")
    modules = parsed.get("modules_affected", [])
    if modules:
        console.print(f"    [dim]Modules:[/dim]     {', '.join(modules)}")
    constraints = parsed.get("implementation_constraints", [])
    if constraints:
        console.print("    [dim]Constraints:[/dim]")
        for c in constraints:
            console.print(f"      • {c}")
    risks = parsed.get("risks", [])
    if risks:
        console.print("    [dim]Risks:[/dim]")
        for r in risks:
            console.print(f"      ⚠ {r}")
    console.print(f"    [dim]DB Impact:[/dim]  {parsed.get('db_impact', '—')}  |  [dim]API Impact:[/dim]  {parsed.get('api_impact', '—')}")


def _review_coder_output(parsed: dict) -> None:
    """Pretty-print coder-agent output for review."""
    console.print(f"    [dim]Summary:[/dim]  {parsed.get('implementation_summary', '—')}")
    created = parsed.get("files_created", [])
    if created:
        console.print("    [dim]Files Created:[/dim]")
        for f in created:
            console.print(f"      + {f}")
    modified = parsed.get("files_modified", [])
    if modified:
        console.print("    [dim]Files Modified:[/dim]")
        for f in modified:
            console.print(f"      ~ {f}")
    tests = parsed.get("tests_added", [])
    if tests:
        console.print("    [dim]Tests:[/dim]")
        for t in tests:
            console.print(f"      🧪 {t}")
    tc = parsed.get("test_coverage", {})
    if tc:
        h = "✅" if tc.get("happy_path") else "❌"
        e = "✅" if tc.get("edge_case") else "❌"
        er = "✅" if tc.get("error_handling") else "❌"
        console.print(f"    [dim]Coverage:[/dim]  Happy {h}  Edge {e}  Error {er}")
    followups = parsed.get("follow_ups", [])
    if followups:
        console.print("    [dim]Follow-ups:[/dim]")
        for f in followups:
            console.print(f"      → {f}")
    # Show code blocks summary (not full content)
    blocks = parsed.get("code_blocks", [])
    if blocks:
        console.print(f"    [dim]Code Blocks:[/dim]  {len(blocks)} files")
        for b in blocks:
            lines = b.get("content", "").count("\n") + 1
            console.print(f"      📄 {b.get('path', '?')} ({b.get('action', '?')}, {lines} lines)")


_REVIEWERS: dict[str, Callable[[dict], None]] = {
    "spec-agent": _review_spec_output,
    "architect-agent": _review_architect_output,
    "coder-agent": _review_coder_output,
}


def print_run_review(
    run: dict,
    logs: list[dict],
    gates: list[dict],
    *,
    flow: str,
    timeline: str,
    trust_package: dict | None,
    trust_issues: list[str],
    parse_output: Callable[[str], dict | None],
    show_raw: bool = False,
) -> None:
    """`factory review <run_id>`: header, flow, timeline, trust package, then each
    agent in order with the gate that followed it."""
    run_id = run["id"]
    # Header
    console.print()
    status = run["status"]
    style = "green" if status == "completed" else "red"
    console.print(Rule(f"[bold]📊 Review: Run #{run_id}[/bold]"))
    console.print(f"  [dim]Story:[/dim]   {run['story_id']}")
    console.print(f"  [dim]Status:[/dim]  [{style}]{status.upper()}[/{style}]")
    console.print(f"  [dim]Started:[/dim] {run['started_at']}")
    if run.get("finished_at"):
        console.print(f"  [dim]Finished:[/dim] {run['finished_at']}")
    console.print()

    # Original request
    console.print(Panel(run["request"], title="📋 Original Request", border_style="cyan"))
    console.print()

    # Pipeline flow + chronological timeline
    if flow:
        console.print(f"  {flow}\n")
    if timeline:
        console.print(Panel(timeline, title="🕒 Timeline", border_style="dim"))
        console.print()

    # Trust package (the release sign-off evidence)
    pkg = trust_package
    if pkg:
        sb = pkg["security_boundary"]
        tick = lambda b: "[green]✓[/green]" if b else "[red]✗[/red]"  # noqa: E731
        lines = [
            f"Verdict: [bold]{pkg['verdict']}[/bold]  ·  next: {pkg['next_authorization']}",
            f"{tick(pkg['tests']['passed'])} tests passed  ·  AC covered: {len(pkg['tests']['ac_coverage'])}",
            f"Files changed: {len(pkg['diff']['files'])}  ·  ADR: {pkg['adr']['path'] or '—'}",
            f"Security: {sb['overall']} (highest: {sb['highest_severity']})"
            + (f" — {sb['findings']}" if sb['findings'] else ""),
            f"Cost: ${pkg['cost']['usd']:.4f}  ·  {pkg['cost']['tokens_in']}→{pkg['cost']['tokens_out']} tok",
        ]
        if trust_issues:
            lines.append(f"[red]schema issues: {trust_issues}[/red]")
        border = "green" if pkg["next_authorization"] == "release" else "yellow"
        console.print(Panel("\n".join(lines), title="📦 Trust Package", border_style=border))
        console.print()

    # Walk through each agent + gate in order
    gate_idx = 0
    total_cost = 0.0
    total_tokens_in = 0
    total_tokens_out = 0
    for log in logs:
        agent = log["agent"]
        v = log.get("verdict") or "—"
        dur = f" ({log['duration_secs']:.1f}s)" if log.get("duration_secs") else ""
        console.print(f"  🤖 [bold cyan]{agent}[/bold cyan] → [{_verdict_style(v)}]{v.upper()}[/{_verdict_style(v)}]{dur}")

        # Cost / provenance line (if captured)
        cost = log.get("cost_usd")
        t_in = log.get("tokens_in")
        t_out = log.get("tokens_out")
        if cost is not None or t_in is not None or t_out is not None:
            total_cost += cost or 0.0
            total_tokens_in += t_in or 0
            total_tokens_out += t_out or 0
            parts = []
            if t_in is not None or t_out is not None:
                parts.append(f"{t_in or 0}→{t_out or 0} tok")
            if cost is not None:
                parts.append(f"${cost:.4f}")
            if log.get("model_name"):
                parts.append(log["model_name"])
            console.print(f"    [dim]{' · '.join(parts)}[/dim]")

        # Show what was SENT to the agent
        if log.get("input_text"):
            inp = log["input_text"]
            if len(inp) > 300:
                inp = inp[:300] + "..."
            console.print(f"    [dim italic]Prompt:[/dim italic] {inp}")

        # Show structured output
        if log.get("output_text"):
            parsed = parse_output(log["output_text"])
            if parsed and not show_raw:
                reviewer = _REVIEWERS.get(agent)
                if reviewer:
                    reviewer(parsed)
            elif show_raw:
                text = log["output_text"]
                if len(text) > 2000:
                    text = text[:2000] + "\n... (truncated, use --full for complete output)"
                console.print(Panel(text, title=f"{agent} raw output", border_style="dim"))

        # Show gate that follows this agent
        if gate_idx < len(gates):
            gate = gates[gate_idx]
            passed = bool(gate["passed"])
            icon = _gate_icon(passed)
            gstyle = "green" if passed else "red"
            console.print(f"\n  {icon} [bold]{gate['gate_name']}[/bold]: [{gstyle}]{gate['reason']}[/{gstyle}]")
            gate_idx += 1

        console.print()

    if total_cost or total_tokens_in or total_tokens_out:
        console.print(
            f"  [bold]Run total:[/bold] {total_tokens_in}→{total_tokens_out} tok · "
            f"[bold]${total_cost:.4f}[/bold]"
        )
        console.print()

    if run.get("error"):
        console.print(Panel(run["error"], title="❌ Error", border_style="red"))


# ── Queue / board / list ──────────────────────────────────────────


def print_queue(
    parked: list[tuple[dict, dict | None, float]], attention: list[tuple[dict, float]]
) -> None:
    """Parked runs awaiting sign-off, plus runs that need attention."""
    if not parked and not attention:
        console.print("[dim]Queue is empty — nothing awaiting review.[/dim]")
        return

    if parked:
        console.print(Rule("[bold]⏸  Awaiting your sign-off[/bold]"))
        for run, gate, cost in parked:
            stage = run.get("current_stage", "?")
            title = run.get("story_title") or run["story_id"]
            console.print(
                f"  [bold]#{run['id']}[/bold] [cyan]{run['story_id']}[/cyan] "
                f"{title}  [dim](stage: {stage}, spent ${cost:.4f})[/dim]"
            )
            if gate and gate.get("reason"):
                console.print(f"    [dim]why:[/dim] {gate['reason']}")
            if gate and gate.get("human_questions"):
                for q in gate["human_questions"].split("\n\n"):
                    if q.strip():
                        console.print(f"    [yellow]?[/yellow] {q.strip()}")
            console.print(
                f"    [green]factory approve {run['id']}[/green] · "
                f"[red]factory reject {run['id']} \"<feedback>\"[/red]"
            )
        console.print()

    if attention:
        console.print(Rule("[bold]⚠  Needs attention[/bold]"))
        for run, cost in attention:
            title = run.get("story_title") or run["story_id"]
            err = (run.get("error") or "").strip()
            if len(err) > 100:
                err = err[:100] + "…"
            console.print(
                f"  [bold]#{run['id']}[/bold] [cyan]{run['story_id']}[/cyan] {title}  "
                f"[{_verdict_style(run['status'])}]{run['status'].upper()}[/{_verdict_style(run['status'])}] "
                f"[dim](spent ${cost:.4f})[/dim]"
            )
            if err:
                console.print(f"    [dim]{err}[/dim]")
            console.print(f"    [dim]factory review {run['id']} · factory replay {run['id']}[/dim]")
        console.print()


def _elapsed(iso_start: str) -> str:
    from datetime import datetime, timezone

    try:
        start = datetime.fromisoformat(iso_start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        secs = int((datetime.now(timezone.utc) - start).total_seconds())
    except (ValueError, TypeError):
        return "?"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


def board_renderable(
    active: list[tuple[dict, dict | None, float]], attention_rows: list[tuple[dict, float]]
) -> Group:
    """A snapshot renderable of the factory's current state (the plain board)."""
    blocks: list = [Rule("[bold]🏭 Factory Board[/bold]")]

    active_tbl = Table(title="In flight", border_style="cyan", expand=True)
    active_tbl.add_column("#", justify="right", style="bold")
    active_tbl.add_column("Story", style="cyan")
    active_tbl.add_column("State")
    active_tbl.add_column("Stage / waiting on")
    active_tbl.add_column("Elapsed", justify="right")
    active_tbl.add_column("Cost", justify="right")
    active_tbl.add_column("Your move", style="green")
    if not active:
        active_tbl.add_row("—", "[dim]nothing running[/dim]", "", "", "", "", "")
    for run, gate, cost in active:
        parked = run["status"] == "waiting_human"
        state = "[yellow]⏸ NEEDS YOU[/yellow]" if parked else "[cyan]running[/cyan]"
        detail = (gate.get("reason", "")[:48] if gate else run.get("current_stage", "?"))
        move = (
            f"approve {run['id']}  ·  reject {run['id']} \"…\"" if parked else "[dim]wait[/dim]"
        )
        active_tbl.add_row(
            str(run["id"]), run.get("story_title") or run["story_id"], state,
            detail, _elapsed(run["started_at"]), f"${cost:.4f}", move,
        )
    blocks.append(active_tbl)

    # Detail of what each parked run is asking
    for run, gate, _ in active:
        if run["status"] == "waiting_human" and gate and gate.get("human_questions"):
            qs = [q.strip() for q in gate["human_questions"].split("\n\n") if q.strip()]
            body = "\n".join(f"[yellow]?[/yellow] {q}" for q in qs)
            blocks.append(
                Panel(body, title=f"⏸ Run #{run['id']} needs your input", border_style="yellow")
            )

    if attention_rows:
        att = Table(title="Needs attention", border_style="red", expand=True)
        att.add_column("#", justify="right", style="bold")
        att.add_column("Story", style="cyan")
        att.add_column("Status")
        att.add_column("Why")
        att.add_column("Your move", style="dim")
        for run, cost in attention_rows[:10]:
            err = (run.get("error") or "").strip().replace("\n", " ")
            if len(err) > 60:
                err = err[:60] + "…"
            att.add_row(
                str(run["id"]), run.get("story_title") or run["story_id"],
                f"[{_verdict_style(run['status'])}]{run['status']}[/{_verdict_style(run['status'])}]",
                err, f"review {run['id']} · replay {run['id']}",
            )
        blocks.append(att)

    blocks.append(Text("commands: factory approve/reject <#> · review <#> · replay <#>", style="dim"))
    return Group(*blocks)


def print_runs(rows: list[dict]) -> None:
    """`factory list`: every pipeline run."""
    if not rows:
        console.print("[dim]No runs found. Run a pipeline first.[/dim]")
        return

    table = Table(title="📦 Pipeline Runs", border_style="cyan")
    table.add_column("#", style="bold", justify="right")
    table.add_column("Story", style="cyan")
    table.add_column("Request")
    table.add_column("Status", justify="center")
    table.add_column("Last Stage")
    table.add_column("Started", style="dim")

    for row in rows:
        req = row["request"]
        if len(req) > 60:
            req = req[:57] + "..."
        st = row["status"]
        style = _verdict_style(st)
        table.add_row(
            str(row["id"]),
            row["story_id"],
            req,
            Text(st.upper(), style=style),
            row["current_stage"],
            row["started_at"][:19],
        )

    console.print(table)
    console.print("\n  [dim]Use[/dim] factory review <id> [dim]to inspect a run[/dim]")
    console.print("  [dim]Use[/dim] factory review <id> --raw [dim]to see full agent output[/dim]")


# ── Projects / specs ──────────────────────────────────────────────


def print_project_created(project: dict) -> None:
    console.print(Panel(
        f"[bold green]{project['id']}[/bold green] {project['name']}\n"
        f"Slug: {project['slug']}\n"
        f"Repo: {project['repo_path']}",
        title="Project Created",
        border_style="green",
    ))


def print_projects(projects: list[dict]) -> None:
    if not projects:
        console.print("[dim]No projects found. Use factory project create <slug> first.[/dim]")
        return

    table = Table(title="Projects", border_style="cyan")
    table.add_column("ID", style="cyan")
    table.add_column("Slug")
    table.add_column("Name")
    table.add_column("Status", justify="center")
    table.add_column("Repo", style="dim")
    for project in projects:
        table.add_row(
            project["id"],
            project["slug"],
            project["name"],
            project["status"],
            project["repo_path"],
        )
    console.print(table)


def print_project(project: dict) -> None:
    table = Table(title=f"Project {project['id']}", show_header=False, border_style="cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for key in ("id", "slug", "name", "status", "repo_path", "spec_path"):
        table.add_row(key, project.get(key) or "")
    console.print(table)


def print_created(path: Path, title: str) -> None:
    """A green panel naming a file a command just wrote."""
    console.print(Panel(str(path), title=title, border_style="green"))


# ── Self-test: tiers / simulate / evals / metrics ─────────────────

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
    console.print(table)
    console.print(
        "[dim]Override a tier without editing code: "
        "FACTORY_TIER_FRONTIER=provider/model factory run ...[/dim]"
    )


def print_report_written(path: Path) -> None:
    console.print(f"  [dim]Report written to {path}[/dim]")


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
        flow = re.sub(r"\[/?[a-z0-9 #]*\]", "", r.flow_text)
        table.add_row(r.scenario.name, r.scenario.expected_status, actual, ok, flow[:60])
    console.print(table)
    passed = sum(1 for r in results if r.passed)
    style = "green" if passed == len(results) else "red"
    console.print(f"  [{style}]{passed}/{len(results)} scenarios behaving as expected[/{style}]")


def print_eval_captured(run_id: int, path: Path) -> None:
    console.print(f"[green]Captured run #{run_id} as a permanent eval case:[/green] {path}")


def print_eval_replayed(result: Any) -> None:
    """Whether a just-captured eval case reproduces its recorded outcome."""
    icon = "[green]✅[/green]" if result.passed else "[red]❌[/red]"
    console.print(f"  {icon} replays to '{result.actual}' (expected '{result.expected}')")
    if not result.passed:
        console.print(f"  [red]{result.detail}[/red]")


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
    console.print(table)

    passed = len(report.results) - len(report.failures)
    style = "green" if report.passed else "red"
    console.print(
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
    console.print("[bold]factory doctor[/bold] — preflight before a run spends tokens")
    for c in report.checks:
        icon = _DOCTOR_ICON.get(c.status, c.status)
        console.print(f"  {icon} [bold]{c.name}[/bold]  [dim]{c.detail}[/dim]")
    if report.passed:
        console.print("  [green]Ready: every blocking check passed.[/green]")
    else:
        console.print(
            "  [red]Not ready: fix the ❌ lines above before `factory run` "
            "(re-point a tier with FACTORY_TIER_<TIER>=provider/model or edit "
            "agents/tiers.toml).[/red]"
        )


def print_metrics(m: Any, db_path: Path) -> None:
    """The playbook's SDLC indicators (`evidence.metrics.Metrics`), NOT MEASURABLE last."""
    console.print()
    console.print(Rule(f"[bold blue]Factory Metrics[/bold blue]  [dim]{db_path}[/dim]",
                       style="blue"))

    throughput = Table(title="Throughput", border_style="cyan")
    throughput.add_column("Status", style="bold")
    throughput.add_column("Runs", justify="right")
    for status, n in sorted(m.by_status.items(), key=lambda kv: -kv[1]):
        throughput.add_row(status, str(n))
    throughput.add_row("[bold]total[/bold]", f"[bold]{m.total_runs}[/bold]")
    console.print(throughput)
    console.print(f"  Completion rate: [bold]{round(m.completion_rate * 100)}%[/bold]\n")

    quality = Table(title="First-pass quality", border_style="cyan")
    quality.add_column("Gate", style="bold")
    quality.add_column("Runs", justify="right")
    quality.add_column("Pass rate", justify="right")
    for gate in sorted(m.gate_pass_rate):
        rate = m.gate_pass_rate[gate]
        colour = "green" if rate >= 0.8 else ("yellow" if rate >= 0.5 else "red")
        quality.add_row(gate, str(m.gate_counts.get(gate, 0)),
                        f"[{colour}]{round(rate * 100)}%[/{colour}]")
    console.print(quality)
    console.print(
        f"  First-pass rate (no coder retry): [bold]{round(m.first_pass_rate * 100)}%[/bold]"
        f"   ·   coder retries: [bold]{m.total_coder_retries}[/bold]\n"
    )

    console.print(
        f"  [bold]Trust per interruption[/bold]: {m.checkpoints_reached} checkpoint(s) "
        f"({m.checkpoints_answered} answered, {m.checkpoints_pending} pending) "
        f"— {m.runs_per_checkpoint:.1f} runs per interruption\n"
    )

    if m.cost_measurable:
        console.print(
            f"  [bold]Cost[/bold]: ${m.total_cost_usd:.4f} "
            f"({m.total_tokens_in:,} in / {m.total_tokens_out:,} out tokens, "
            f"{round(m.cost_coverage * 100)}% of calls instrumented)\n"
        )

    console.print(Panel(
        "\n".join(f"• {n}" for n in m.not_measurable),
        title="⚠️  NOT MEASURABLE", border_style="yellow",
    ))
