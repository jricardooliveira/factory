#!/usr/bin/env python3
"""Stress-test the pipeline with scenarios designed to expose failure modes.

Each scenario targets a specific type of breakage or human-input need.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from rich.console import Console
from rich.rule import Rule

from factory.domain.project_spec import ProjectSpec
from factory.interfaces.cli.common import DB_PATH
from factory.interfaces.cli.run import RunPrinter
from factory.runs import run_pipeline
from factory.state.db import init_db

console = Console()

SPEC_FILE = Path(__file__).resolve().parents[3] / "examples" / "specs" / "taskflow.json"

SCENARIOS = [
    # ── Category 1: Requirements that contradict the project spec ──
    {
        "name": "🔴 Contradicts spec — asks for WebSockets",
        "why": "Request explicitly wants WebSockets but spec forbids them. Should the spec-agent refuse? Or the architect?",
        "request": "Add real-time notifications using WebSockets so users see updates instantly without polling.",
    },
    {
        "name": "🔴 Contradicts spec — asks for GraphQL",
        "why": "Spec forbids GraphQL. Who catches this?",
        "request": "Build a GraphQL API layer on top of the existing REST endpoints so the frontend can fetch exactly the data it needs.",
    },

    # ── Category 2: Ambiguous trade-offs requiring human decision ──
    {
        "name": "🟡 Trade-off — security vs usability",
        "why": "Architect must decide: strict 2FA for everyone, or optional? This is a product decision, not a technical one.",
        "request": "Add two-factor authentication. Some users have complained about security, but others find extra login steps annoying.",
    },
    {
        "name": "🟡 Trade-off — breaking change",
        "why": "Changing the task status enum breaks existing API consumers. Architect should flag this needs human approval.",
        "request": "Add a new task status 'blocked' and rename 'in_progress' to 'active' across the whole system.",
    },

    # ── Category 3: Scope too large for one pipeline run ──
    {
        "name": "🟠 Scope explosion",
        "why": "This is really 5+ stories. Spec-agent should either refuse or split it, not cram it into one.",
        "request": "Build a complete project management system with sprints, epics, story points, burndown charts, team velocity tracking, resource allocation, time tracking, and Gantt charts.",
    },

    # ── Category 4: Needs domain knowledge the agents don't have ──
    {
        "name": "🟡 Domain-specific — compliance",
        "why": "GDPR compliance requires specific legal/domain knowledge. Agents shouldn't guess at this.",
        "request": "Make our user data handling GDPR compliant. We have EU users and need to handle data deletion requests, consent tracking, and data portability exports.",
    },

    # ── Category 5: Depends on external system state ──
    {
        "name": "🟡 External dependency",
        "why": "Integrating with Stripe requires API keys, sandbox accounts, webhook endpoints — things agents can't verify.",
        "request": "Add Stripe payment processing so users can upgrade to a premium plan with monthly billing.",
    },

    # ── Category 6: Bug report with insufficient info ──
    {
        "name": "🔴 Incomplete bug report",
        "why": "No reproduction steps, no error message, no context. Spec-agent should refuse.",
        "request": "The app is broken. Fix it.",
    },
]


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    spec_data = json.loads(SPEC_FILE.read_text())
    project_spec = ProjectSpec.model_validate(spec_data)
    spec_text = project_spec.to_architect_context()
    repo_root = str(Path(__file__).resolve().parents[3])

    for i, scenario in enumerate(SCENARIOS, 1):
        console.print()
        console.print(Rule(f"[bold magenta]Scenario {i}/{len(SCENARIOS)}: {scenario['name']}[/bold magenta]", style="magenta"))
        console.print(f"  [dim italic]Why: {scenario['why']}[/dim italic]")
        console.print()

        try:
            init_db(DB_PATH)
            run_pipeline(scenario["request"], opencode_cwd=repo_root, project_spec_text=spec_text,
                         db_path=DB_PATH, on_event=RunPrinter())
        except Exception as e:
            console.print(f"  [bold red]CRASH:[/bold red] {e}")

        console.print()
        time.sleep(2)

    console.print(Rule("[bold green]All scenarios complete[/bold green]", style="green"))


if __name__ == "__main__":
    main()
