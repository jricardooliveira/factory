"""`factory interview <project>`: the operator defines the product in the terminal.

`factory.runs` drives the interview; this module only supplies the two things it
cannot do without printing — asking a question, getting the brief and the stack
approved. `--amend` reopens an approved brief; `--import` records answers given in
the /factory-intake skill.
"""

from __future__ import annotations

import json
from pathlib import Path

from factory import runs
from factory.domain.backlog import BacklogStory
from factory.domain.interview import InterviewQuestion
from factory.domain.project_spec import ProjectSpec
from factory.interfaces import render
from factory.interfaces.cli.common import db_path, fail
from factory.interfaces.render.backlog import (
    BACKLOG_APPROVE_PROMPT,
    print_backlog_approved,
    print_backlog_not_approved,
    print_backlog_proposal,
)
from factory.interfaces.render.interview import (
    BACKLOG_REPROPOSE_PROMPT,
    STACK_PROMPT,
    print_brief_already_approved,
    print_stack_proposal,
)
from factory.runs import InterviewOutcome, RunError
from factory.runs.interview import Ask


def _read(prompt: str = "> ") -> str:
    # markup=False: the prompt contains "[y]es", which rich would otherwise eat.
    return render.console.input(prompt, markup=False).strip()


def _verdict(prompt: str) -> bool | str:
    """y -> True, n -> False, anything else is the operator's own words."""
    while not (raw := _read(f"{prompt}: ")):
        pass
    if raw.lower() in ("y", "yes"):
        return True
    if raw.lower() in ("n", "no"):
        return False
    return raw


def _asker() -> Ask:
    said_done = False

    def ask(question: InterviewQuestion, missing: list[str]) -> str | None:
        nonlocal said_done
        # The service only asks again after "done" when required topics are open:
        # say which, or the operator just sees their "done" ignored.
        if said_done and missing:
            render.print_topics_still_required(missing)
            said_done = False
        render.print_interview_question(question)
        while not (raw := _read()):
            pass
        if raw.lower() == "done":
            said_done = True
            return None
        return raw

    return ask


def _approve(brief: str) -> bool | str:
    render.print_brief_for_approval(brief)
    return _verdict(render.APPROVE_PROMPT)


def _confirm_stack(spec: ProjectSpec) -> bool | str:
    print_stack_proposal(spec)
    return _verdict(STACK_PROMPT)


def interview(project_ref: str, *, amend: str | None = None) -> InterviewOutcome:
    """Run the interview on this terminal and print how it ended."""
    try:
        outcome = runs.run_interview(project_ref, db_path=db_path(), ask=_asker(),
                                     approve=_approve, confirm_stack=_confirm_stack,
                                     amend=amend)
    except (RunError, ValueError) as exc:
        fail(str(exc))
    if outcome.approved:
        render.print_interview_approved(outcome.brief_path, None if amend else project_ref)
    else:
        render.print_interview_paused(project_ref, outcome.answers)
    return outcome


def story_interview(project_ref: str, request: str) -> str:
    """Ask what the brief leaves open for this story; the request with the answers."""
    try:
        return runs.run_story_interview(project_ref, request, db_path=db_path(), ask=_asker())
    except (RunError, ValueError) as exc:
        fail(str(exc))


def _import(project_ref: str, file: str) -> None:
    path = Path(file)
    if not path.is_file():
        fail(f"Answers file not found: {file}")
    try:
        answers = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        fail(f"Answers file is not valid JSON: {exc}")
    try:
        outcome = runs.import_answers(project_ref, answers, db_path=db_path())
    except (RunError, ValueError) as exc:
        fail(str(exc))
    render.print_interview_approved(outcome.brief_path)


def _repropose_backlog(project_ref: str) -> None:
    """`factory backlog`'s service and screens; not its module (cli.backlog imports this one)."""

    def review(stories: list[BacklogStory]) -> bool | str:
        print_backlog_proposal(stories)
        return _verdict(BACKLOG_APPROVE_PROMPT)

    try:
        outcome = runs.propose_backlog(project_ref, db_path=db_path(), review=review)
    except (RunError, ValueError) as exc:
        fail(str(exc))
    if outcome.approved:
        print_backlog_approved(outcome.stories, project_ref)
    else:
        print_backlog_not_approved(project_ref)


def interview_command(args: list[str]) -> None:
    usage = ('Usage: factory interview <project-id-or-slug> '
             '[--amend "what changed" | --import answers.json]')
    if len(args) == 1:
        project_ref = args[0]
        if runs.has_brief(project_ref, db_path=db_path()):
            print_brief_already_approved(project_ref)
            return
        interview(project_ref)
    elif len(args) == 3 and args[1] == "--import":
        _import(args[0], args[2])
    elif len(args) >= 3 and args[1] == "--amend" and (amend := " ".join(args[2:]).strip()):
        outcome = interview(args[0], amend=amend)
        # Started stories keep their requests by design; only unstarted ones are re-proposed.
        if outcome.approved and runs.next_story(args[0], db_path=db_path()) is not None:
            if _verdict(BACKLOG_REPROPOSE_PROMPT) is True:
                _repropose_backlog(args[0])
    else:
        fail(usage)
