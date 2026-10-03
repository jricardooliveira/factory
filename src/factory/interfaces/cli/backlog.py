"""`factory backlog <project>` and `factory next <project>`: the story list, one story at a time.

`factory.runs` proposes and stores the backlog; this module only shows the
proposal, reads the operator's verdict, and starts the next story with RunPrinter.
"""

from __future__ import annotations

import sys

from factory import runs
from factory.domain.backlog import BacklogStory
from factory.interfaces import render
from factory.interfaces.cli.common import db_path, fail
from factory.interfaces.cli.interview import story_interview
from factory.interfaces.cli.run import RunPrinter
from factory.interfaces.render.backlog import (
    BACKLOG_APPROVE_PROMPT,
    print_backlog_approved,
    print_backlog_empty,
    print_backlog_not_approved,
    print_backlog_proposal,
    print_next_story,
)
from factory.runs import RunError, RunOutcome


def backlog_command(args: list[str]) -> None:
    if len(args) != 1:
        fail("Usage: factory backlog <project-id-or-slug>")

    def review(stories: list[BacklogStory]) -> bool | str:
        print_backlog_proposal(stories)
        # markup=False: the prompt contains "[y]es", which rich would otherwise eat.
        while not (raw := render.console.input(f"{BACKLOG_APPROVE_PROMPT}: ",
                                               markup=False).strip()):
            pass
        if raw.lower() in ("y", "yes"):
            return True
        if raw.lower() in ("n", "no"):
            return False
        return raw

    try:
        outcome = runs.propose_backlog(args[0], db_path=db_path(), review=review)
    except (RunError, ValueError) as exc:
        fail(str(exc))
    if outcome.approved:
        print_backlog_approved(outcome.stories, args[0])
    else:
        print_backlog_not_approved(args[0])


def start_story(project_ref: str, request: str, *, ask_first: bool = True) -> RunOutcome:
    """The one place `factory next` starts a run, after the story-level interview."""
    if ask_first and sys.stdin.isatty() and runs.has_brief(project_ref, db_path=db_path()):
        request = story_interview(project_ref, request)
    return runs.run_project_pipeline(project_ref, request, db_path=db_path(), on_event=RunPrinter())


def next_command(args: list[str]) -> None:
    ask_first = "--no-interview" not in args
    args = [a for a in args if a != "--no-interview"]
    if len(args) != 1:
        fail("Usage: factory next <project-id-or-slug> [--no-interview]")
    try:
        row = runs.next_story(args[0], db_path=db_path())
        if row is None:
            print_backlog_empty(args[0])
            return
        print_next_story(row)
        outcome = start_story(args[0], row["request"], ask_first=ask_first)
        runs.mark_started(row["id"], story_id=outcome.story_id, run_id=outcome.run_id,
                          db_path=db_path())
    except (RunError, ValueError) as exc:
        fail(str(exc))
