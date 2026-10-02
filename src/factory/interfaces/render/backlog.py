"""`factory backlog` / `factory next` output: the proposed story list, the story started."""

from __future__ import annotations

from typing import Any

from rich.markup import escape

from factory.domain.backlog import BacklogStory
from factory.interfaces.render import output

BACKLOG_APPROVE_PROMPT = "Approve? [y]es / [n]o / or type feedback"


def print_backlog_proposal(stories: list[BacklogStory]) -> None:
    # Agent text is escaped: a "[x]" in a request is not rich markup.
    output.console.print("\n[bold cyan]Proposed backlog[/bold cyan]")
    for number, story in enumerate(stories, start=1):
        output.console.print(f"\n[bold]{number}. {escape(story.title)}[/bold]")
        output.console.print(f"   {escape(story.request)}")
        if story.rationale:
            output.console.print(f"   [dim]Why: {escape(story.rationale)}[/dim]")


def print_backlog_approved(stories: int) -> None:
    output.console.print(
        f"[green]Backlog approved[/green] — {stories} story(ies) in docs/work/BACKLOG.md. "
        "Start the first with: factory next <project>"
    )


def print_backlog_not_approved(project_ref: str) -> None:
    output.console.print(
        f"[yellow]Backlog not approved[/yellow] — nothing was saved. "
        f"Try again with: factory backlog {escape(project_ref)}"
    )


def print_backlog_empty(project_ref: str) -> None:
    output.console.print(
        f"[yellow]backlog empty[/yellow] — no approved story left. "
        f"Propose more with: factory backlog {escape(project_ref)}"
    )


def print_next_story(row: dict[str, Any]) -> None:
    output.console.print(
        f"[bold cyan]Starting story {row['position']}:[/bold cyan] {escape(row['title'])}"
    )
