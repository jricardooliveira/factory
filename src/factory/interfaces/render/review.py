"""`factory review`: everything a run did, agent by agent, plus the flow and timeline markup."""

from __future__ import annotations

from collections.abc import Callable

from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule

from factory.evidence.progress import (
    FLOW_ARROW,
    StageStatus,
    TimelineEvent,
    hms,
    stage_text,
)
from factory.interfaces.render import output
from factory.interfaces.render.board import print_decision_commands, print_gate_questions


def _review_spec_output(parsed: dict) -> None:
    """Pretty-print spec-agent output for review."""
    output.console.print(f"    [dim]Title:[/dim]    {parsed.get('title', '—')}")
    output.console.print(f"    [dim]Type:[/dim]     {parsed.get('type', '—')}")
    output.console.print(f"    [dim]Problem:[/dim]  {parsed.get('problem', '—')}")
    ac = parsed.get("acceptance_criteria", [])
    if ac:
        output.console.print("    [dim]AC:[/dim]")
        for a in ac:
            output.console.print(f"      • {a}")
    tasks = parsed.get("tasks", [])
    if tasks:
        output.console.print("    [dim]Tasks:[/dim]")
        for t in tasks:
            tid = t.get("id", "?")
            title = t.get("title", "?")
            output.console.print(f"      • {tid}: {title}")
    questions = parsed.get("questions", [])
    if questions:
        output.console.print("    [dim]Open Questions:[/dim]")
        for q in questions:
            output.console.print(f"      ❓ {q}")


def _review_architect_output(parsed: dict) -> None:
    """Pretty-print architect-agent output for review."""
    notes = parsed.get("architecture_notes", "—")
    if len(notes) > 400:
        notes = notes[:400] + "..."
    output.console.print(f"    [dim]Notes:[/dim]       {notes}")
    modules = parsed.get("modules_affected", [])
    if modules:
        output.console.print(f"    [dim]Modules:[/dim]     {', '.join(modules)}")
    constraints = parsed.get("implementation_constraints", [])
    if constraints:
        output.console.print("    [dim]Constraints:[/dim]")
        for c in constraints:
            output.console.print(f"      • {c}")
    risks = parsed.get("risks", [])
    if risks:
        output.console.print("    [dim]Risks:[/dim]")
        for r in risks:
            output.console.print(f"      ⚠ {r}")
    output.console.print(f"    [dim]DB Impact:[/dim]  {parsed.get('db_impact', '—')}  |  [dim]API Impact:[/dim]  {parsed.get('api_impact', '—')}")


def _review_coder_output(parsed: dict) -> None:
    """Pretty-print coder-agent output for review."""
    output.console.print(f"    [dim]Summary:[/dim]  {parsed.get('implementation_summary', '—')}")
    created = parsed.get("files_created", [])
    if created:
        output.console.print("    [dim]Files Created:[/dim]")
        for f in created:
            output.console.print(f"      + {f}")
    modified = parsed.get("files_modified", [])
    if modified:
        output.console.print("    [dim]Files Modified:[/dim]")
        for f in modified:
            output.console.print(f"      ~ {f}")
    tests = parsed.get("tests_added", [])
    if tests:
        output.console.print("    [dim]Tests:[/dim]")
        for t in tests:
            output.console.print(f"      🧪 {t}")
    tc = parsed.get("test_coverage", {})
    if tc:
        h = "✅" if tc.get("happy_path") else "❌"
        e = "✅" if tc.get("edge_case") else "❌"
        er = "✅" if tc.get("error_handling") else "❌"
        output.console.print(f"    [dim]Coverage:[/dim]  Happy {h}  Edge {e}  Error {er}")
    followups = parsed.get("follow_ups", [])
    if followups:
        output.console.print("    [dim]Follow-ups:[/dim]")
        for f in followups:
            output.console.print(f"      → {f}")
    # Show code blocks summary (not full content)
    blocks = parsed.get("code_blocks", [])
    if blocks:
        output.console.print(f"    [dim]Code Blocks:[/dim]  {len(blocks)} files")
        for b in blocks:
            lines = b.get("content", "").count("\n") + 1
            output.console.print(f"      📄 {b.get('path', '?')} ({b.get('action', '?')}, {lines} lines)")


def _print_list(label: str, items: list, mark: str = "•") -> None:
    if items:
        output.console.print(f"    [dim]{label}:[/dim]")
        for item in items:
            output.console.print(f"      {mark} {item}")


def _review_tester_output(parsed: dict) -> None:
    """Pretty-print tester-agent output: the three sub-verdicts, then why."""
    output.console.print(
        f"    [dim]QA:[/dim] {parsed.get('qa_verdict', '—')}  |  "
        f"[dim]Security:[/dim] {parsed.get('security_verdict', '—')} "
        f"(highest: {parsed.get('highest_severity', '—')})  |  "
        f"[dim]Performance:[/dim] {parsed.get('performance_verdict', '—')}"
    )
    output.console.print(f"    [dim]Summary:[/dim]  {parsed.get('summary') or '—'}")
    _print_list("Missing coverage", parsed.get("missing_coverage", []), "⚠")
    _print_list("Security findings", parsed.get("security_findings", []), "⚠")
    _print_list("Performance findings", parsed.get("performance_findings", []), "⚠")


def _review_release_output(parsed: dict) -> None:
    """Pretty-print release-agent output: what ships, how to check it, how to undo it."""
    output.console.print(f"    [dim]Summary:[/dim]  {parsed.get('summary') or '—'}")
    _print_list("How to verify", parsed.get("how_to_verify", []))
    if parsed.get("migration_notes") not in (None, "", "none"):
        output.console.print(f"    [dim]Migration:[/dim] {parsed['migration_notes']}")
    output.console.print(f"    [dim]Rollback:[/dim] {parsed.get('rollback_notes') or '—'}")
    _print_list("Known limitations", parsed.get("known_limitations", []), "⚠")
    _print_list("Concerns", parsed.get("concerns", []), "⚠")


def _review_boundary_output(parsed: dict) -> None:
    """Pretty-print boundary-agent output: its four sub-verdicts with their findings."""
    for key in ("tenant", "authorization", "api_contract", "security"):
        sub = parsed.get(key) or {}
        output.console.print(f"    [dim]{key}:[/dim] {sub.get('verdict', '—')}")
        for f in sub.get("findings", []) + sub.get("breaking_changes", []):
            output.console.print(f"      ⚠ {f}")
    _print_list("Required changes", parsed.get("required_changes", []), "→")


_REVIEWERS: dict[str, Callable[[dict], None]] = {
    "spec-agent": _review_spec_output,
    "architect-agent": _review_architect_output,
    "boundary-agent": _review_boundary_output,
    "coder-agent": _review_coder_output,
    "tester-agent": _review_tester_output,
    "release-agent": _review_release_output,
}


def _print_review_header(run: dict) -> None:
    output.console.print()
    status = run["status"]
    style = "green" if status == "completed" else "red"
    output.console.print(Rule(f"[bold]📊 Review: Run #{run['id']}[/bold]"))
    if status == "waiting_human":
        style = "yellow"
    output.console.print(f"  [dim]Story:[/dim]   {run['story_id']}")
    output.console.print(f"  [dim]Status:[/dim]  [{style}]{status.upper()}[/{style}]")
    output.console.print(f"  [dim]Started:[/dim] {run['started_at']}")
    if run.get("finished_at"):
        output.console.print(f"  [dim]Finished:[/dim] {run['finished_at']}")
    output.console.print()

    # Original request
    output.console.print(Panel(run["request"], title="📋 Original Request", border_style="cyan"))
    output.console.print()


def _ac_covered(pkg: dict) -> str:
    """"covered/total", or "not yet measured" while every criterion is unassessed
    (no tester has run) — counting the criteria themselves overstated coverage."""
    entries = pkg["tests"]["ac_coverage"]
    trace = pkg.get("ac_traceability") or {}
    if not entries or all(e.get("status") == "unassessed" for e in entries):
        return "not yet measured"
    if trace.get("total"):
        return f"{trace['covered']}/{trace['total']}"
    return str(len(entries))  # no spec criteria: the tester's own list


def _print_trust_package(pkg: dict, trust_issues: list[str]) -> None:
    """The release sign-off evidence, one panel."""
    sb = pkg["security_boundary"]
    tick = lambda b: "[green]✓[/green]" if b else "[red]✗[/red]"  # noqa: E731
    lines = [
        f"Verdict: [bold]{pkg['verdict']}[/bold]  ·  next: {pkg['next_authorization']}",
        f"{tick(pkg['tests']['passed'])} tests passed  ·  AC covered: {_ac_covered(pkg)}",
        f"Files changed: {len(pkg['diff']['files'])}  ·  ADR: {pkg['adr']['path'] or '—'}",
        f"Security: {sb['overall']} (highest: {sb['highest_severity']})"
        + (f" — {sb['findings']}" if sb['findings'] else ""),
        f"Cost: ${pkg['cost']['usd']:.4f}  ·  {pkg['cost']['tokens_in']}→{pkg['cost']['tokens_out']} tok",
    ]
    lines += [f"[yellow]⚠ {escape(b)}[/yellow]" for b in pkg.get("blockers", [])]
    if trust_issues:
        lines.append(f"[red]schema issues: {trust_issues}[/red]")
    border = "green" if pkg["next_authorization"] == "release" else "yellow"
    output.console.print(Panel("\n".join(lines), title="📦 Trust Package", border_style=border))
    output.console.print()


def _print_usage_line(log: dict) -> tuple[float, int, int]:
    """The cost / provenance line of one agent call (if captured); returns
    (cost, tokens in, tokens out) to add to the run total."""
    cost = log.get("cost_usd")
    t_in = log.get("tokens_in")
    t_out = log.get("tokens_out")
    if cost is None and t_in is None and t_out is None:
        return 0.0, 0, 0
    parts = []
    if t_in is not None or t_out is not None:
        parts.append(f"{t_in or 0}→{t_out or 0} tok")
    if cost is not None:
        parts.append(f"${cost:.4f}")
    if log.get("model_name"):
        parts.append(log["model_name"])
    output.console.print(f"    [dim]{' · '.join(parts)}[/dim]")
    return cost or 0.0, t_in or 0, t_out or 0


def _print_agent_output(
    log: dict, parse_output: Callable[[str], dict | None], show_raw: bool
) -> None:
    """What was SENT to the agent, then its structured (or raw) output."""
    agent = log["agent"]
    if show_raw and log.get("input_text"):
        inp = log["input_text"]
        if len(inp) > 300:
            inp = inp[:300] + "..."
        output.console.print(f"    [dim italic]Prompt:[/dim italic] {inp}")

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
            output.console.print(Panel(text, title=f"{agent} raw output", border_style="dim"))


def _print_gate(gate: dict) -> None:
    passed = bool(gate["passed"])
    parked = gate.get("needs_human") and not gate.get("human_response")
    icon = "⏸" if parked else output.gate_icon(passed)
    gstyle = "yellow" if parked else ("green" if passed else "red")
    output.console.print(
        f"\n  {icon} [bold]{gate['gate_name']}[/bold]: [{gstyle}]{escape(gate['reason'])}[/{gstyle}]"
    )
    if parked:
        print_gate_questions(gate)


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
    _print_review_header(run)

    # Pipeline flow + chronological timeline
    if flow:
        output.console.print(f"  {flow}\n")
    if timeline:
        output.console.print(Panel(timeline, title="🕒 Timeline", border_style="dim"))
        output.console.print()

    # Before any code exists the package can only list what is missing, which at
    # Checkpoint 1/2 reads as a failure of work nobody has done yet.
    if trust_package and any(log["agent"] == "coder-agent" for log in logs):
        _print_trust_package(trust_package, trust_issues)

    # Walk through each agent + gate in order
    gate_idx = 0
    total_cost = 0.0
    total_tokens_in = 0
    total_tokens_out = 0
    for log in logs:
        agent = log["agent"]
        v = log.get("verdict") or "—"
        dur = f" ({log['duration_secs']:.1f}s)" if log.get("duration_secs") else ""
        style = output.verdict_style(v)
        output.console.print(
            f"  🤖 [bold cyan]{agent}[/bold cyan] → [{style}]{v.upper()}[/{style}]{dur}"
        )
        cost, t_in, t_out = _print_usage_line(log)
        total_cost += cost
        total_tokens_in += t_in
        total_tokens_out += t_out
        _print_agent_output(log, parse_output, show_raw)

        # Show gate that follows this agent
        if gate_idx < len(gates):
            _print_gate(gates[gate_idx])
            gate_idx += 1

        output.console.print()
    # ponytail: gates pair with agents by position; any left over (e.g. a retry's
    # extra gate-build) still print, so a parked gate's question is never hidden.
    for gate in gates[gate_idx:]:
        _print_gate(gate)

    if total_cost or total_tokens_in or total_tokens_out:
        output.console.print(
            f"  [bold]Run total:[/bold] {total_tokens_in}→{total_tokens_out} tok · "
            f"[bold]${total_cost:.4f}[/bold]"
        )
        output.console.print()

    if run.get("error"):
        output.console.print(Panel(run["error"], title="❌ Error", border_style="red"))
    if run["status"] == "waiting_human":
        output.console.print(Rule("[bold]⏸  Your decision[/bold]"))
        print_decision_commands(run["id"])


# ── flow + timeline markup (from evidence.progress data) ─────────

_STAGE_STYLE = {
    "done": "green",
    "failed": "red",
    "waiting": "yellow",
    "current": "cyan",
    "pending": "dim",
}


def render_flow(stages: list[StageStatus]) -> str:
    """One-line agent pipeline flow with per-stage status icons (rich markup).

    The same text as `progress.plain_flow`, with each stage and arrow styled.
    """
    if not stages:
        return ""
    parts = []
    for s in stages:
        style = _STAGE_STYLE.get(s.status, "dim")
        parts.append(f"[{style}]{stage_text(s)}[/{style}]")
    return f"  [dim]{FLOW_ARROW.strip()}[/dim]  ".join(parts)


_TIMELINE_ICON = {"done": ("✓", "green"), "failed": ("✗", "red"),
                  "waiting": ("⏸", "yellow"), "info": ("•", "cyan")}


def render_timeline(events: list[TimelineEvent]) -> str:
    """Render a timeline as multi-line rich markup."""
    lines = []
    for e in events:
        icon, style = _TIMELINE_ICON.get(e.status, ("•", "dim"))
        detail = f"  [dim]{e.detail}[/dim]" if e.detail else ""
        lines.append(f"[dim]{hms(e.when)}[/dim] [{style}]{icon}[/{style}] {e.label}{detail}")
    return "\n".join(lines)
