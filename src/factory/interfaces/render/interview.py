"""`factory interview` output: one question at a time, then the brief to approve."""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from rich.panel import Panel

from factory.domain.interview import OTHER_TITLE, TOPIC_TITLES, InterviewQuestion
from factory.domain.project_spec import ProjectSpec
from factory.interfaces.render import output

ANSWER_HINT = "(number, your own words, 'you decide', or 'done')"
APPROVE_PROMPT = "Approve? [y]es / [n]o, stop for now / or type what is wrong"
STACK_PROMPT = "Use this stack? [y]es / [n]o, keep the current one / or type what to change"
BACKLOG_REPROPOSE_PROMPT = "Re-propose the unstarted backlog stories now? [y]es / [n]o"


def _title(topic: str) -> str:
    return TOPIC_TITLES.get(topic, OTHER_TITLE)


def print_interview_question(question: InterviewQuestion) -> None:
    # Agent and operator text is escaped: a "[x]" in a question is not rich markup.
    output.console.print(f"\n[bold cyan]{escape(_title(question.topic))}[/bold cyan]")
    output.console.print(f"[bold]{escape(question.question)}[/bold]")
    for number, option in enumerate(question.options, start=1):
        detail = f" [dim]— {escape(option.description)}[/dim]" if option.description else ""
        output.console.print(f"  {number}. {escape(option.label)}{detail}")
    output.console.print(f"[dim]{ANSWER_HINT}[/dim]")


def print_topics_still_required(topics: list[str]) -> None:
    names = ", ".join(_title(t) for t in topics)
    output.console.print(f"\n[yellow]Not done yet — still required: {names}[/yellow]")


def print_brief_for_approval(brief: str) -> None:
    output.console.print(Panel(escape(brief), title="Product brief", border_style="cyan"))


def print_interview_approved(brief_path: Path | None) -> None:
    output.print_created(brief_path, "Product Brief Approved")


def print_interview_paused(project_ref: str, answers: int) -> None:
    output.console.print(
        f"[yellow]No brief approved yet[/yellow] — {answers} answer(s) saved. "
        f"Resume with: factory interview {escape(project_ref)}"
    )


def print_stack_proposal(spec: ProjectSpec) -> None:
    output.console.print(
        Panel(escape(spec.to_architect_context()), title="Proposed stack", border_style="cyan")
    )


def print_brief_already_approved(project_ref: str) -> None:
    ref = escape(project_ref)
    output.console.print(
        f"[green]{ref} already has an approved product brief.[/green] To change it: "
        f'factory interview {ref} --amend "what changed"'
    )
