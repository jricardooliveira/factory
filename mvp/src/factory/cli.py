"""Rich CLI for the factory pipeline — watchable, reviewable output."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from pydantic import ValidationError
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from factory.models import ArchitectOutput, CoderOutput, SpecOutput
from factory.pipeline import PipelineState, compile_pipeline
from factory.projects import create_project, get_project, list_projects as fetch_projects
from factory.spec_templates import create_project_spec, write_project_spec
from factory.notify import notify
from factory.utils import parse_agent_json
from factory.state.db import (
    archive_run,
    create_story,
    get_db,
    get_pending_human_gate,
    get_run_cost,
    get_run_gates,
    get_run_logs,
    get_runs_by_status,
    init_db,
    reconcile_stale_runs,
    respond_to_gate,
    start_run,
)

console = Console()

DB_PATH = Path("factory.db")


def print_usage(exit_code: int = 1) -> None:
    console.print("[bold]Usage:[/bold]")
    console.print("  factory [bold cyan]\"Your request here\"[/bold cyan]          Run pipeline")
    console.print("  factory [bold cyan]run --project <id> \"...\"[/bold cyan]      Run pipeline for project")
    console.print("  factory [bold cyan]spec init <slug> --stack fastapi[/bold cyan] Create project spec")
    console.print("  factory [bold cyan]project create <slug>[/bold cyan]          Create/register project")
    console.print("  factory [bold cyan]project list[/bold cyan]                   List projects")
    console.print("  factory [bold cyan]project show <id>[/bold cyan]              Show project")
    console.print("  factory [bold cyan]list[/bold cyan]                           List all runs")
    console.print("  factory [bold cyan]queue[/bold cyan]                          Show runs awaiting review / needing attention")
    console.print("  factory [bold cyan]board[/bold cyan]                          Interactive board: approve/reject in place ([dim]--once / --plain[/dim])")
    console.print("  factory [bold cyan]review <run_id>[/bold cyan]               Review a run")
    console.print("  factory [bold cyan]review <run_id> --raw[/bold cyan]         Review with raw output")
    console.print("  factory [bold cyan]replay <run_id>[/bold cyan]               Re-run orchestration on frozen outputs (no LLM)")
    console.print("  factory [bold cyan]visualize [--output path][/bold cyan]      Generate HTML flow report")
    console.print("  factory [bold cyan]dismiss <run_id>[/bold cyan]              Archive a run off the board")
    console.print("  factory [bold cyan]reconcile [--older-than S][/bold cyan]     Fail runs stuck 'running' (dead process)")
    console.print("  factory [bold cyan]simulate [--report path][/bold cyan]        Offline scenario matrix (no tokens)")
    console.print("  factory [bold cyan]approve <run_id>[/bold cyan]              Approve paused run")
    console.print("  factory [bold cyan]reject <run_id> [reason][/bold cyan]      Reject paused run")
    console.print("  factory [bold cyan]tiers[/bold cyan]                          Show per-agent model tiers (leverage allocation)")
    sys.exit(exit_code)


def show_tiers() -> None:
    """Print which model each agent runs at — the factory's leverage allocation."""
    from factory import model_tiers as mt

    table = Table(title="Model tiers — frontier reserved for high-leverage thinking")
    table.add_column("Agent", style="bold")
    table.add_column("Cognitive work")
    table.add_column("Tier")
    table.add_column("Model", style="dim")
    work = {
        "spec-agent": "planning / decomposition",
        "architect-agent": "design trade-offs, risk, ADRs",
        "tester-agent": "risk + security / QA review",
        "coder-agent": "implementation",
    }
    tier_color = {"frontier": "magenta", "standard": "cyan", "fast": "green"}
    for agent, tier in mt.AGENT_TIERS.items():
        color = tier_color.get(tier, "white")
        escalates = agent in mt.ESCALATE_ON_RETRY
        tier_label = f"[{color}]{tier}[/{color}]"
        model = mt.model_for_tier(tier)
        if escalates:
            esc_tier = mt.ESCALATE_ON_RETRY[agent]
            tier_label += f" [dim]→ {esc_tier} on retry[/dim]"
            model += f"  [dim](retry: {mt.model_for_tier(esc_tier)})[/dim]"
        table.add_row(agent, work.get(agent, ""), tier_label, model)
    console.print(table)
    console.print(
        "[dim]Override a tier without editing code: "
        "FACTORY_TIER_FRONTIER=provider/model factory run ...[/dim]"
    )


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


def print_review_table(run_id: int, db_path: Path) -> None:
    """Print a review table of all agent logs and gates for a run."""
    with get_db(db_path) as conn:
        logs = get_run_logs(conn, run_id)
        gates = get_run_gates(conn, run_id)

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


def run_pipeline(
    request: str,
    opencode_cwd: str | None = None,
    project_spec_text: str | None = None,
    db_path: Path = DB_PATH,
    project_id: str | None = None,
    replay_run_id: int | None = None,
) -> None:
    """Run the full pipeline with rich output."""
    print_header(request)

    if replay_run_id:
        console.print(f"  [yellow]↻ Replaying frozen outputs from run #{replay_run_id} (no opencode calls)[/yellow]\n")

    if project_spec_text:
        console.print(Panel(project_spec_text, title="📐 Project Spec", border_style="magenta"))
        console.print()

    # Init DB; create a new story (fresh run) or reuse the replayed run's story
    init_db(db_path)
    with get_db(db_path) as conn:
        if replay_run_id:
            orig = conn.execute(
                "SELECT story_id FROM pipeline_runs WHERE id = ?", (replay_run_id,)
            ).fetchone()
            story_id = orig["story_id"]
        else:
            # Auto-increment story ID
            row = conn.execute("SELECT COUNT(*) as c FROM stories").fetchone()
            n = row["c"] + 1
            story_id = f"US-{n:04d}"
            create_story(conn, story_id, "Pending", request, project_id=project_id)
        run_id = start_run(conn, story_id, project_id=project_id)

    console.print(f"  📦 Run [bold]#{run_id}[/bold] | Story [bold]{story_id}[/bold]\n")

    # Build and run graph
    pipeline = compile_pipeline()
    initial_state: PipelineState = {
        "request": request,
        "story_id": story_id,
        "run_id": run_id,
        "db_path": str(db_path),
        "opencode_cwd": opencode_cwd or str(Path.cwd()),
    }
    if project_spec_text:
        initial_state["project_spec"] = project_spec_text
    # Project runs get a project_dir (parent of repo/) so ADRs + decision memory work.
    if project_id and opencode_cwd:
        initial_state["project_dir"] = str(Path(opencode_cwd).parent)
    if replay_run_id:
        initial_state["replay_run_id"] = replay_run_id

    # Stream through the graph nodes for live output
    final_state = dict(initial_state)
    for event in pipeline.stream(initial_state):
        for node_name, node_output in event.items():
            final_state.update(node_output)

            if node_name == "spec-agent":
                if node_output.get("status") in ("failed", "blocked"):
                    print_agent_done("spec-agent", node_output.get("status", "error"), 0)
                else:
                    spec = node_output.get("spec", {})
                    v = spec.get("verdict", "unknown")
                    print_agent_done("spec-agent", v, 0)
                    print_spec_summary(spec)

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

    # Final summary
    status = final_state.get("status", "unknown")
    human_qs = final_state.get("gate_2", {}).get("human_questions") if status == "waiting_human" else None
    print_final_status(status, final_state.get("error"), human_questions=human_qs)
    print_review_table(run_id, db_path)
    _notify_if_parked(run_id, story_id, status, final_state.get("current_stage"))


def _load_project_spec_text(project: dict) -> str | None:
    spec_path = project.get("spec_path")
    if not spec_path:
        return None

    from factory.project_spec import ProjectSpec

    path = Path(spec_path)
    spec_data = json.loads(path.read_text())
    return ProjectSpec.model_validate(spec_data).to_architect_context()


def run_project_pipeline(project_ref: str, request: str, db_path: Path = DB_PATH) -> None:
    """Run the pipeline for a registered project."""

    project = get_project(db_path, project_ref)
    run_pipeline(
        request,
        opencode_cwd=project["repo_path"],
        project_spec_text=_load_project_spec_text(project),
        db_path=db_path,
        project_id=project["id"],
    )


# ── Review command ────────────────────────────────────────────────

def _review_spec_output(parsed: dict) -> None:
    """Pretty-print spec-agent output for review."""
    console.print(f"    [dim]Title:[/dim]    {parsed.get('title', '—')}")
    console.print(f"    [dim]Type:[/dim]     {parsed.get('type', '—')}")
    console.print(f"    [dim]Problem:[/dim]  {parsed.get('problem', '—')}")
    ac = parsed.get("acceptance_criteria", [])
    if ac:
        console.print(f"    [dim]AC:[/dim]")
        for a in ac:
            console.print(f"      • {a}")
    tasks = parsed.get("tasks", [])
    if tasks:
        console.print(f"    [dim]Tasks:[/dim]")
        for t in tasks:
            tid = t.get("id", "?")
            title = t.get("title", "?")
            console.print(f"      • {tid}: {title}")
    questions = parsed.get("questions", [])
    if questions:
        console.print(f"    [dim]Open Questions:[/dim]")
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
        console.print(f"    [dim]Constraints:[/dim]")
        for c in constraints:
            console.print(f"      • {c}")
    risks = parsed.get("risks", [])
    if risks:
        console.print(f"    [dim]Risks:[/dim]")
        for r in risks:
            console.print(f"      ⚠ {r}")
    console.print(f"    [dim]DB Impact:[/dim]  {parsed.get('db_impact', '—')}  |  [dim]API Impact:[/dim]  {parsed.get('api_impact', '—')}")


def _review_coder_output(parsed: dict) -> None:
    """Pretty-print coder-agent output for review."""
    console.print(f"    [dim]Summary:[/dim]  {parsed.get('implementation_summary', '—')}")
    created = parsed.get("files_created", [])
    if created:
        console.print(f"    [dim]Files Created:[/dim]")
        for f in created:
            console.print(f"      + {f}")
    modified = parsed.get("files_modified", [])
    if modified:
        console.print(f"    [dim]Files Modified:[/dim]")
        for f in modified:
            console.print(f"      ~ {f}")
    tests = parsed.get("tests_added", [])
    if tests:
        console.print(f"    [dim]Tests:[/dim]")
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
        console.print(f"    [dim]Follow-ups:[/dim]")
        for f in followups:
            console.print(f"      → {f}")
    # Show code blocks summary (not full content)
    blocks = parsed.get("code_blocks", [])
    if blocks:
        console.print(f"    [dim]Code Blocks:[/dim]  {len(blocks)} files")
        for b in blocks:
            lines = b.get("content", "").count("\n") + 1
            console.print(f"      📄 {b.get('path', '?')} ({b.get('action', '?')}, {lines} lines)")


def _parse_agent_output(raw: str) -> dict | None:
    """Extract JSON from agent output text (shared parser)."""
    return parse_agent_json(raw)


def replay_run(run_id: int, db_path: Path = DB_PATH) -> None:
    """Re-run the orchestration against a past run's frozen agent outputs."""
    init_db(db_path)
    with get_db(db_path) as conn:
        orig = conn.execute(
            "SELECT pr.*, s.request FROM pipeline_runs pr "
            "JOIN stories s ON pr.story_id = s.id WHERE pr.id = ?",
            (run_id,),
        ).fetchone()
        if not orig:
            console.print(f"[red]No run found with id #{run_id}[/red]")
            sys.exit(1)
        orig = dict(orig)

    project_spec_text = None
    opencode_cwd = None
    if orig.get("project_id"):
        project = get_project(db_path, orig["project_id"])
        if project:
            opencode_cwd = project["repo_path"]
            project_spec_text = _load_project_spec_text(project)

    run_pipeline(
        orig["request"],
        opencode_cwd=opencode_cwd,
        project_spec_text=project_spec_text,
        db_path=db_path,
        project_id=orig.get("project_id"),
        replay_run_id=run_id,
    )


def review_run(run_id: int, show_raw: bool = False) -> None:
    """Review a past pipeline run from the database."""
    with get_db(DB_PATH) as conn:
        run = conn.execute(
            "SELECT pr.*, s.request, s.title as story_title FROM pipeline_runs pr JOIN stories s ON pr.story_id = s.id WHERE pr.id = ?",
            (run_id,),
        ).fetchone()
        if not run:
            console.print(f"[red]No run found with id #{run_id}[/red]")
            return
        run = dict(run)
        logs = get_run_logs(conn, run_id)
        gates = get_run_gates(conn, run_id)

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
    from factory.board_data import (
        render_flow,
        render_timeline,
        run_pipeline_progress,
        run_timeline,
    )

    flow = render_flow(run_pipeline_progress(DB_PATH, run_id))
    if flow:
        console.print(f"  {flow}\n")
    timeline = render_timeline(run_timeline(DB_PATH, run_id))
    if timeline:
        console.print(Panel(timeline, title="🕒 Timeline", border_style="dim"))
        console.print()

    # Trust package (the release sign-off evidence)
    from factory import trust_package as _tp

    pkg = _tp.assemble(DB_PATH, run_id)
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
        issues = _tp.validate(pkg)
        if issues:
            lines.append(f"[red]schema issues: {issues}[/red]")
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
            parsed = _parse_agent_output(log["output_text"])
            if parsed and not show_raw:
                if agent == "spec-agent":
                    _review_spec_output(parsed)
                elif agent == "architect-agent":
                    _review_architect_output(parsed)
                elif agent == "coder-agent":
                    _review_coder_output(parsed)
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


def _notify_if_parked(run_id: int, story_id: str, status: str, stage: str | None = None) -> None:
    """Ping the operator when a run parks for review or escalates a failure."""
    if status == "waiting_human":
        notify(
            "Factory: needs your review",
            f"Run #{run_id} {story_id} parked at {stage or 'a checkpoint'} - factory queue",
        )
    elif status in ("failed", "blocked"):
        notify(
            "Factory: run needs attention",
            f"Run #{run_id} {story_id} {status} - factory queue",
        )


def show_queue() -> None:
    """Show parked runs awaiting sign-off, plus runs that need attention."""
    init_db(DB_PATH)
    with get_db(DB_PATH) as conn:
        parked = get_runs_by_status(conn, ["waiting_human"])
        attention = get_runs_by_status(conn, ["failed", "blocked"])
        parked_ctx = []
        for run in parked:
            gate = get_pending_human_gate(conn, run["id"])
            parked_ctx.append((run, gate, get_run_cost(conn, run["id"])))
        attention_ctx = [(run, get_run_cost(conn, run["id"])) for run in attention]

    if not parked_ctx and not attention_ctx:
        console.print("[dim]Queue is empty — nothing awaiting review.[/dim]")
        return

    if parked_ctx:
        console.print(Rule("[bold]⏸  Awaiting your sign-off[/bold]"))
        for run, gate, cost in parked_ctx:
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

    if attention_ctx:
        console.print(Rule("[bold]⚠  Needs attention[/bold]"))
        for run, cost in attention_ctx:
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


def render_board():
    """Build a snapshot renderable of the factory's current state."""
    from rich.console import Group

    with get_db(DB_PATH) as conn:
        running = get_runs_by_status(conn, ["running", "waiting_human"])
        attention = get_runs_by_status(conn, ["failed", "blocked"])
        active = [
            (r, get_pending_human_gate(conn, r["id"]) if r["status"] == "waiting_human" else None,
             get_run_cost(conn, r["id"]))
            for r in running
        ]
        attention_rows = [(r, get_run_cost(conn, r["id"])) for r in attention]

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


def show_board(once: bool = False, interval: float = 2.0) -> None:
    """Live status board. --once prints a single snapshot."""
    init_db(DB_PATH)
    if once:
        console.print(render_board())
        return
    console.print("[dim]Live board — press Ctrl-C to exit.[/dim]")
    try:
        with Live(render_board(), console=console, refresh_per_second=4, screen=False) as live:
            while True:
                time.sleep(interval)
                live.update(render_board())
    except KeyboardInterrupt:
        console.print("\n[dim]board closed[/dim]")


def list_runs() -> None:
    """List all pipeline runs."""
    init_db(DB_PATH)
    with get_db(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT pr.id, pr.story_id, s.request, pr.status, pr.current_stage, pr.started_at
            FROM pipeline_runs pr
            JOIN stories s ON pr.story_id = s.id
            ORDER BY pr.id
        """).fetchall()

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
        row = dict(row)
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
    console.print(f"\n  [dim]Use[/dim] factory review <id> [dim]to inspect a run[/dim]")
    console.print(f"  [dim]Use[/dim] factory review <id> --raw [dim]to see full agent output[/dim]")


# ── Project commands ──────────────────────────────────────────────

def create_project_command(args: list[str]) -> None:
    """Create and register a factory project."""

    if not args:
        console.print("[red]Usage: factory project create <slug> [--name NAME] [--spec PATH] [--stack fastapi][/red]")
        sys.exit(1)

    slug = args[0]
    name = None
    spec_path = None
    stack = None
    i = 1
    while i < len(args):
        if args[i] == "--name" and i + 1 < len(args):
            name = args[i + 1]
            i += 2
        elif args[i] == "--spec" and i + 1 < len(args):
            spec_path = Path(args[i + 1]).resolve()
            i += 2
        elif args[i] == "--stack" and i + 1 < len(args):
            stack = args[i + 1]
            i += 2
        else:
            console.print(f"[red]Unknown project create option: {args[i]}[/red]")
            sys.exit(1)

    init_db(DB_PATH)
    try:
        project = create_project(
            DB_PATH,
            factory_root=Path.cwd(),
            slug=slug,
            name=name,
            spec_path=spec_path,
            stack=stack,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    console.print(Panel(
        f"[bold green]{project['id']}[/bold green] {project['name']}\n"
        f"Slug: {project['slug']}\n"
        f"Repo: {project['repo_path']}",
        title="Project Created",
        border_style="green",
    ))


def list_projects_command() -> None:
    """List registered factory projects."""

    projects = fetch_projects(DB_PATH)
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


def show_project_command(ref: str) -> None:
    """Print one registered project."""

    try:
        project = get_project(DB_PATH, ref)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    table = Table(title=f"Project {project['id']}", show_header=False, border_style="cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for key in ("id", "slug", "name", "status", "repo_path", "spec_path"):
        table.add_row(key, project.get(key) or "")
    console.print(table)


def project_command(args: list[str]) -> None:
    """Dispatch project subcommands."""

    if not args:
        console.print("[red]Usage: factory project <create|list|show> ...[/red]")
        sys.exit(1)

    subcommand = args[0]
    if subcommand == "create":
        create_project_command(args[1:])
    elif subcommand == "list":
        list_projects_command()
    elif subcommand == "show":
        if len(args) < 2:
            console.print("[red]Usage: factory project show <project-id-or-slug>[/red]")
            sys.exit(1)
        show_project_command(args[1])
    else:
        console.print(f"[red]Unknown project command: {subcommand}[/red]")
        sys.exit(1)


def spec_init_command(args: list[str]) -> None:
    """Create a project specification JSON file from a stack template."""

    if not args:
        console.print("[red]Usage: factory spec init <slug> --stack fastapi [--name NAME] [--output PATH][/red]")
        sys.exit(1)

    slug = args[0]
    name = None
    output = Path("project-spec.json")
    stack = None
    i = 1
    while i < len(args):
        if args[i] == "--stack" and i + 1 < len(args):
            stack = args[i + 1]
            i += 2
        elif args[i] == "--name" and i + 1 < len(args):
            name = args[i + 1]
            i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            output = Path(args[i + 1])
            i += 2
        else:
            console.print(f"[red]Unknown spec init option: {args[i]}[/red]")
            sys.exit(1)

    if not stack:
        console.print("[red]Missing required option: --stack fastapi[/red]")
        sys.exit(1)

    try:
        path = write_project_spec(
            output,
            create_project_spec(stack, name=name, slug=slug),
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    console.print(Panel(str(path), title="Project Spec Created", border_style="green"))


def spec_command(args: list[str]) -> None:
    """Dispatch spec subcommands."""

    if not args:
        console.print("[red]Usage: factory spec init <slug> --stack fastapi[/red]")
        sys.exit(1)

    subcommand = args[0]
    if subcommand == "init":
        spec_init_command(args[1:])
    else:
        console.print(f"[red]Unknown spec command: {subcommand}[/red]")
        sys.exit(1)


def run_command(args: list[str]) -> None:
    """Run a request against a registered project."""

    if len(args) < 3 or args[0] != "--project":
        console.print('[red]Usage: factory run --project <project-id-or-slug> "Your request here"[/red]')
        sys.exit(1)

    project_ref = args[1]
    request = " ".join(args[2:]).strip()
    if not request:
        console.print("[red]Run request cannot be empty[/red]")
        sys.exit(1)

    try:
        run_project_pipeline(project_ref, request)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)


def visualize_command(args: list[str]) -> None:
    """Generate a static HTML report for factory state."""

    output = Path("factory-visualization.html")
    i = 0
    while i < len(args):
        if args[i] == "--output" and i + 1 < len(args):
            output = Path(args[i + 1])
            i += 2
        else:
            console.print(f"[red]Unknown visualize option: {args[i]}[/red]")
            sys.exit(1)

    from factory.visualization import generate_factory_visualization

    path = generate_factory_visualization(DB_PATH, output)
    console.print(Panel(str(path), title="Factory Visualization Created", border_style="green"))


# ── Resume command (approve / reject) ─────────────────────────────

def resume_run(run_id: int, action: str, reason: str | None = None) -> None:
    """Resume a paused pipeline run after human approval/rejection."""
    init_db(DB_PATH)
    with get_db(DB_PATH) as conn:
        run = conn.execute(
            "SELECT pr.*, s.request FROM pipeline_runs pr JOIN stories s ON pr.story_id = s.id WHERE pr.id = ?",
            (run_id,),
        ).fetchone()
        if not run:
            console.print(f"[red]No run found with id #{run_id}[/red]")
            return
        run = dict(run)

        if run["status"] != "waiting_human":
            console.print(f"[red]Run #{run_id} is not waiting for human approval (status: {run['status']})[/red]")
            return

        pending = get_pending_human_gate(conn, run_id)
        if not pending:
            console.print(f"[red]No pending human gate found for run #{run_id}[/red]")
            return

        # Record the human decision on the pending gate and reopen the run.
        decision = reason or (
            "Rejected by human reviewer" if action == "reject" else "Approved by human reviewer"
        )
        verdict_word = "REJECTED" if action == "reject" else "APPROVED"
        respond_to_gate(conn, pending["id"], f"{verdict_word}: {decision}")
        conn.execute("UPDATE pipeline_runs SET status = 'running' WHERE id = ?", (run_id,))
        conn.commit()

    # Reconstruct context from the run's logs.
    with get_db(DB_PATH) as conn:
        logs = get_run_logs(conn, run_id)
    spec_log = next((l for l in logs if l["agent"] == "spec-agent"), None)
    arch_log = next((l for l in logs if l["agent"] == "architect-agent"), None)
    if not spec_log:
        console.print("[red]Cannot resume: missing spec log[/red]")
        return
    spec_parsed = _parse_agent_output(spec_log["output_text"]) or {}

    opencode_cwd = str(Path.cwd())
    project_spec_text = None
    project_dir = None
    if run.get("project_id"):
        project = get_project(DB_PATH, run["project_id"])
        opencode_cwd = project["repo_path"]
        project_spec_text = _load_project_spec_text(project)
        project_dir = str(Path(opencode_cwd).parent)

    base_state: dict[str, Any] = {
        "request": run["request"],
        "story_id": run["story_id"],
        "run_id": run_id,
        "db_path": str(DB_PATH),
        "opencode_cwd": opencode_cwd,
        "spec": spec_parsed,
        "status": "running",
    }
    if project_spec_text:
        base_state["project_spec"] = project_spec_text
    if project_dir:
        base_state["project_dir"] = project_dir

    if action == "reject":
        # Rejection is NOT a dead end: re-enter architecture with the feedback.
        console.print(Panel(
            f"Run #{run_id} [bold yellow]REJECTED[/bold yellow] — re-running architecture "
            f"with your feedback...\n\n{decision}",
            border_style="yellow",
        ))
        from factory.pipeline import compile_architect_resume_pipeline

        pipeline = compile_architect_resume_pipeline()
        state: dict[str, Any] = {
            **base_state,
            "prior_findings": [decision],
            "triggered_by": "architecture-rejected",
        }
    else:
        if not arch_log:
            console.print("[red]Cannot resume: missing architect log[/red]")
            return
        console.print(Panel(
            f"Run #{run_id} [bold green]APPROVED[/bold green] — continuing to coder-agent...",
            border_style="green",
        ))
        from factory.pipeline import compile_coder_only_pipeline

        pipeline = compile_coder_only_pipeline()
        state = {**base_state, "architect": _parse_agent_output(arch_log["output_text"]) or {}}

    final_state = state
    for event in pipeline.stream(state, stream_mode="updates"):
        for node_name, node_output in event.items():
            final_state = {**final_state, **node_output}
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

    status = final_state.get("status", "unknown")
    human_qs = (
        final_state.get("gate_2", {}).get("human_questions")
        if status == "waiting_human"
        else None
    )
    print_final_status(status, final_state.get("error"), human_questions=human_qs)
    print_review_table(run_id, DB_PATH)
    _notify_if_parked(run_id, run["story_id"], status, final_state.get("current_stage"))

    status = final_state.get("status", "unknown")
    print_final_status(status, final_state.get("error"))
    print_review_table(run_id, DB_PATH)


# ── Entry point ───────────────────────────────────────────────────

def _run_id_arg(usage: str) -> int:
    """Parse sys.argv[2] as a run id, exiting with a friendly error if invalid."""
    if len(sys.argv) < 3:
        console.print(f"[red]{usage}[/red]")
        sys.exit(1)
    try:
        return int(sys.argv[2])
    except ValueError:
        console.print(f"[red]Run id must be a number, got '{sys.argv[2]}'. {usage}[/red]")
        sys.exit(1)


def dismiss_run(run_id: int) -> None:
    """Archive a run off the board (non-destructive)."""
    init_db(DB_PATH)
    with get_db(DB_PATH) as conn:
        row = conn.execute("SELECT id FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            console.print(f"[red]No run found with id #{run_id}[/red]")
            sys.exit(1)
        archive_run(conn, run_id)
    console.print(f"[green]Run #{run_id} dismissed (archived).[/green]")


def simulate_command(args: list[str]) -> None:
    """Run the offline scenario matrix (deterministic, no tokens) + report."""
    import re

    from factory.simulate import render_markdown, simulate_all

    results = simulate_all()
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

    if "--report" in args:
        idx = args.index("--report")
        path = Path(args[idx + 1]) if idx + 1 < len(args) else Path("simulation-report.md")
        path.write_text(render_markdown(results), encoding="utf-8")
        console.print(f"  [dim]Report written to {path}[/dim]")


def reconcile_command(args: list[str]) -> None:
    """Mark stale 'running' runs (process died) as failed."""
    older_than = 3600.0
    if "--older-than" in args:
        idx = args.index("--older-than")
        if idx + 1 < len(args):
            try:
                older_than = float(args[idx + 1])
            except ValueError:
                console.print("[red]--older-than expects seconds (a number)[/red]")
                sys.exit(1)
    init_db(DB_PATH)
    with get_db(DB_PATH) as conn:
        ids = reconcile_stale_runs(conn, older_than_secs=older_than)
    if ids:
        console.print(f"[yellow]Reconciled {len(ids)} stale run(s): {ids}[/yellow]")
    else:
        console.print(f"[dim]No runs stuck 'running' longer than {int(older_than)}s.[/dim]")


def main() -> None:
    if len(sys.argv) < 2:
        print_usage(exit_code=1)

    cmd = sys.argv[1]

    if cmd in ("-h", "--help", "help"):
        print_usage(exit_code=0)
    elif cmd == "project":
        project_command(sys.argv[2:])
    elif cmd == "spec":
        spec_command(sys.argv[2:])
    elif cmd == "run":
        run_command(sys.argv[2:])
    elif cmd == "list":
        list_runs()
    elif cmd == "queue":
        show_queue()
    elif cmd == "tiers":
        show_tiers()
    elif cmd == "board":
        interval = 2.0
        if "--interval" in sys.argv:
            idx = sys.argv.index("--interval")
            if idx + 1 < len(sys.argv):
                interval = float(sys.argv[idx + 1])
        if "--once" in sys.argv:
            show_board(once=True)
        elif "--plain" in sys.argv:
            show_board(once=False, interval=interval)
        else:
            try:
                from factory.tui import run_board_tui
                run_board_tui(DB_PATH)
            except ImportError:
                console.print("[yellow]textual not installed — falling back to plain board[/yellow]")
                show_board(once=False, interval=interval)
    elif cmd == "review":
        rid = _run_id_arg("Usage: factory review <run_id> [--raw]")
        show_raw = "--raw" in sys.argv
        review_run(rid, show_raw=show_raw)
    elif cmd == "replay":
        replay_run(_run_id_arg("Usage: factory replay <run_id>"))
    elif cmd == "dismiss":
        dismiss_run(_run_id_arg("Usage: factory dismiss <run_id>"))
    elif cmd == "reconcile":
        reconcile_command(sys.argv[2:])
    elif cmd == "simulate":
        simulate_command(sys.argv[2:])
    elif cmd == "visualize":
        visualize_command(sys.argv[2:])
    elif cmd == "approve":
        rid = _run_id_arg("Usage: factory approve <run_id> [note]")
        reason = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else None
        resume_run(rid, "approve", reason=reason)
    elif cmd == "reject":
        rid = _run_id_arg('Usage: factory reject <run_id> "<feedback>"')
        reason = " ".join(sys.argv[3:]).strip() if len(sys.argv) > 3 else ""
        if not reason:
            console.print('[red]Reject requires feedback: factory reject <run_id> "<feedback>"[/red]')
            sys.exit(1)
        resume_run(rid, "reject", reason=reason)
    else:
        # Collect flags
        spec_file = None
        args = sys.argv[1:]
        filtered_args = []
        i = 0
        while i < len(args):
            if args[i] == "--spec" and i + 1 < len(args):
                spec_file = args[i + 1]
                i += 2
            else:
                filtered_args.append(args[i])
                i += 1

        request = " ".join(filtered_args).strip()
        if not request:
            console.print("[red]Empty request. Usage: factory \"<your request>\"[/red]")
            sys.exit(1)

        # Load project spec if provided
        project_spec_text = None
        if spec_file:
            from pydantic import ValidationError as _ValErr

            from factory.project_spec import ProjectSpec
            spec_path = Path(spec_file)
            if not spec_path.exists():
                console.print(f"[red]Spec file not found: {spec_file}[/red]")
                sys.exit(1)
            try:
                spec_data = json.loads(spec_path.read_text())
                project_spec = ProjectSpec.model_validate(spec_data)
            except json.JSONDecodeError as exc:
                console.print(f"[red]Spec file is not valid JSON: {exc}[/red]")
                sys.exit(1)
            except _ValErr as exc:
                console.print(f"[red]Spec file does not match the project spec schema:[/red]\n{exc}")
                sys.exit(1)
            project_spec_text = project_spec.to_architect_context()

        run_pipeline(request, project_spec_text=project_spec_text)


if __name__ == "__main__":
    main()
