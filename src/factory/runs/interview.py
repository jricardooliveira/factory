"""The intake interview: the operator defines the product before any story exists.

Runs BEFORE the pipeline (it is not a graph node). The interview-agent only
proposes questions; which required topics are still open is decided in
`domain.interview` from the answers actually recorded. Every answer is saved the
moment it is given, so a failed model call or a paused interview resumes where it
stopped. Like the rest of `runs`, nothing here prints: the interface supplies
`ask` and `approve`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

from factory.adapters.claude_sdk import available as claude_sdk_available
from factory.adapters.claude_sdk import run_prompt as claude_sdk_run
from factory.adapters.claude_sdk import sdk_model
from factory.adapters.opencode import AgentResult, run_agent
from factory.agent_config.location import agents_dir
from factory.agent_config.tiers import resolve_model
from factory.domain.agent_output import parse_agent_json
from factory.domain.gates import (
    MAX_INTERVIEW_QUESTIONS,
    MAX_STACK_PROPOSALS,
    MAX_STORY_INTERVIEW_QUESTIONS,
)
from factory.domain.interview import (
    TOPIC_TITLES,
    ImportedAnswer,
    InterviewQuestion,
    InterviewTurn,
    StoryClarification,
    is_undecided,
    fallback_questions,
    resolve_answer,
    uncovered_topics,
    with_clarifications,
)
from factory.domain.project_spec import ProjectSpec
from factory.evidence.brief import (
    brief_path,
    load_brief,
    render_brief,
    render_transcript,
    write_brief,
)
from factory.runs.context import load_project_spec_text
from factory.runs.events import RunError
from factory.state.db import get_db
from factory.state.interviews import add_answer, list_answers, log_turn
from factory.state.projects import set_spec_path
from factory.workspace.git import git_commit_paths
from factory.workspace.layout import store_location
from factory.workspace.projects import SPEC_FILENAME, get_project
from factory.workspace.templates import write_project_spec

AGENT = "interview-agent"
MAX_QUESTIONS_PER_TURN = 4


def _per_turn(answers: list[dict[str, Any]]) -> int:
    # Questions in one turn cannot see each other's answers. Until the operator has
    # said what the product IS, a second question asks what that answer settles
    # (live: "who uses it?" right after "just for me"), so the first turn is one question.
    return MAX_QUESTIONS_PER_TURN if answers else 1

# (question, still-uncovered required topics) -> raw answer; None = operator says "done".
Ask = Callable[[InterviewQuestion, list[str]], str | None]
# brief markdown -> True approve / False stop for now / str = what is wrong with it.
Approve = Callable[[str], bool | str]
# proposed spec -> True write it / False keep the current one / str = what to change.
ConfirmStack = Callable[[ProjectSpec], bool | str]
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class InterviewOutcome:
    project_id: str
    approved: bool
    brief_path: Path | None
    answers: int


def has_brief(project_ref: str, *, db_path: Path) -> bool:
    """BRIEF.md existing in the project repo IS the approval."""
    return brief_path(Path(get_project(db_path, project_ref)["repo_path"])).is_file()


def build_interview_prompt(
    project: dict[str, Any],
    answers: list[dict[str, Any]],
    missing: list[str],
    amendment: str | None = None,
) -> str:
    name = project.get("name") or project["id"]
    remaining = max(MAX_INTERVIEW_QUESTIONS - len(answers), 0)
    parts = [f"# Product: {name}"]
    if spec := load_project_spec_text(project):
        parts.append(f"## Project spec\n\n{spec}")
    transcript = render_transcript(name, answers) if answers else "(nothing asked yet)\n"
    parts.append(f"## Interview so far\n\n{transcript.rstrip()}")
    if amendment:
        parts.append(
            "## Amendment\n\nThe brief was already approved. The operator changed: "
            f"{amendment}\nAsk ONLY about what this change affects, then set done."
        )
    parts.append(
        "## Required topics still uncovered\n\n"
        + ("\n".join(f"- {t} ({TOPIC_TITLES[t]})" for t in missing) or "- (none)")
    )
    parts.append(
        f"You may ask at most {remaining} more question(s) in total, "
        f"and at most {_per_turn(answers)} in this turn."
    )
    return "\n\n".join(parts) + "\n"


def _system_prompt() -> str:
    """agents/interview-agent.md minus its frontmatter: one prompt for both engines."""
    text = (agents_dir() / f"{AGENT}.md").read_text(encoding="utf-8")
    return text.split("---", 2)[2].lstrip() if text.startswith("---") else text


def _ask_agent(
    project: dict[str, Any], prompt: str, model_cls: type[T], *, db_path: Path, resume: str
) -> T:
    """One interview-agent call, parsed into `model_cls`; any failure is a RunError.

    FACTORY_INTERVIEW_ENGINE: "auto" (default) prefers the Claude Agent SDK and
    retries a failed SDK turn on opencode; "sdk" and "opencode" force one engine.
    """
    model, _tier = resolve_model(AGENT)
    engine = os.environ.get("FACTORY_INTERVIEW_ENGINE", "").strip().lower() or "auto"
    if engine not in ("auto", "sdk", "opencode"):
        raise RunError(f"FACTORY_INTERVIEW_ENGINE={engine!r}: use auto, sdk or opencode.")

    def log(result: AgentResult) -> None:
        # Logged before it is judged: a turn that fails below must still be on record.
        with get_db(db_path) as conn:
            log_turn(
                conn,
                project["id"],
                prompt=prompt,
                output_text=result.output,
                model_name=result.model_name or model,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cost_usd=result.cost_usd,
                duration_secs=result.duration_secs,
            )

    def parsed(result: AgentResult) -> T:
        if not result.success:
            raise RunError(f"{AGENT} call failed (exit {result.returncode}). {resume}")
        try:
            return model_cls.model_validate(parse_agent_json(result.output))
        except ValidationError as exc:
            raise RunError(f"{AGENT} returned an unusable response. {resume}") from exc

    if engine == "sdk" or (engine == "auto" and claude_sdk_available()):
        result = claude_sdk_run(_system_prompt(), prompt, model=sdk_model(model),
                                cwd=project["repo_path"], timeout=None)
        log(result)
        try:
            return parsed(result)
        except RunError:
            if engine == "sdk":
                raise
            # auto: a failed or unusable SDK turn is retried, the same prompt, on opencode
    result = run_agent(AGENT, prompt, cwd=project["repo_path"], model=model)
    log(result)
    return parsed(result)


def next_turn(
    project: dict[str, Any],
    answers: list[dict[str, Any]],
    *,
    db_path: Path,
    amendment: str | None = None,
) -> InterviewTurn:
    """One interview-agent call: its next cluster of questions, or `done`."""
    prompt = build_interview_prompt(project, answers, uncovered_topics(answers), amendment)
    turn = _ask_agent(project, prompt, InterviewTurn, db_path=db_path,
                      resume="Answers so far are saved; run the interview again to resume.")
    turn.questions = turn.questions[:_per_turn(answers)]
    return turn


def build_stack_prompt(project: dict[str, Any], answers: list[dict[str, Any]]) -> str:
    name = project.get("name") or project["id"]
    stack = [f"- {a['answer']}" for a in answers if a["topic"] == "stack"]
    return "\n\n".join([
        "# Mode: stack",
        f"# Product: {name}",
        f"## Approved brief\n\n{render_brief(name, answers).rstrip()}",
        f"## Current project spec\n\n{load_project_spec_text(project) or '(none)'}",
        "## The operator's stack answers (honour every one)\n\n"
        + ("\n".join(stack) or "- (none)"),
    ]) + "\n"


def _propose_stack(
    project: dict[str, Any], confirm: ConfirmStack, *, db_path: Path
) -> list[Path]:
    """Propose project-spec.json until confirmed, declined or capped; the paths it wrote."""
    repo = Path(project["repo_path"])
    corrected = False
    written: list[Path] = []
    for _ in range(MAX_STACK_PROPOSALS):
        with get_db(db_path) as conn:
            answers = list_answers(conn, project["id"])
        spec = _ask_agent(project, build_stack_prompt(project, answers), ProjectSpec,
                          db_path=db_path,
                          resume="The brief is approved; the project stack was not changed.")
        verdict = confirm(spec)
        if verdict is True:
            spec_file = Path(project["spec_path"] or repo / SPEC_FILENAME)
            written.append(write_project_spec(spec_file, spec))
            if not project["spec_path"]:
                with get_db(db_path) as conn:
                    set_spec_path(conn, project["id"], store_location(spec_file, db_path))
            break
        if verdict is False:
            break
        with get_db(db_path) as conn:
            add_answer(conn, project["id"], topic="stack",
                       question="Operator correction to the proposed stack", options=[],
                       answer=verdict, assumed=False)
        corrected = True
    if corrected:
        # A stack correction is an answer: the committed brief must carry it too.
        with get_db(db_path) as conn:
            answers = list_answers(conn, project["id"])
        written += write_brief(repo, project.get("name") or project["id"], answers)
    return written


def run_interview(
    project_ref: str,
    *,
    db_path: Path,
    ask: Ask,
    approve: Approve,
    confirm_stack: ConfirmStack | None = None,
    amend: str | None = None,
) -> InterviewOutcome:
    """Interview until the brief is approved, then (with `confirm_stack`) settle the stack.

    An approved brief is only reopened by an `amend`ment, which is recorded first so
    the agent asks just about what it changes.
    """
    project = get_project(db_path, project_ref)
    project_id = project["id"]
    name = project.get("name") or project_id
    repo = Path(project["repo_path"])

    def record(topic: str, question: str, options: list[str], answer: str, assumed: bool) -> None:
        with get_db(db_path) as conn:
            add_answer(conn, project_id, topic=topic, question=question, options=options,
                       answer=answer, assumed=assumed)

    if amend:
        record("correction", "Operator amendment", [], amend, False)
    elif brief_path(repo).is_file():
        with get_db(db_path) as conn:
            count = len(list_answers(conn, project_id))
        return InterviewOutcome(project_id, True, brief_path(repo), count)

    wants_done = False
    while True:
        with get_db(db_path) as conn:
            answers = list_answers(conn, project_id)
        missing = uncovered_topics(answers)

        if wants_done or len(answers) >= MAX_INTERVIEW_QUESTIONS:
            # No model call — and the cap never waives the minimum.
            questions = fallback_questions(missing)
        else:
            turn = next_turn(project, answers, db_path=db_path, amendment=amend)
            # The agent stopping early is only a claim: Python enforces the minimum.
            questions = ([] if turn.done else turn.questions) or fallback_questions(missing)

        if questions:
            before = len(answers)
            for question in questions:
                raw = ask(question, missing)
                if raw is None:
                    if wants_done:  # "done" again while topics are open: paused
                        return InterviewOutcome(project_id, False, None, len(answers))
                    wants_done = True
                    break
                answer, assumed = resolve_answer(question, raw)
                if answer:
                    record(question.topic, question.question,
                           [o.label for o in question.options], answer, assumed)
                    answers.append({})  # only its length is read, for a paused outcome
            else:
                if len(answers) == before:
                    # Nothing was answered, so the transcript is unchanged: looping
                    # would repeat the same (paid) agent call forever. Pause instead.
                    return InterviewOutcome(project_id, False, None, len(answers))
            continue

        verdict = approve(render_brief(name, answers))
        if verdict is True:
            written = write_brief(repo, name, answers)
            git_commit_paths(repo, written, f"factory: product brief {project_id}")
            if confirm_stack is not None:
                if stack := _propose_stack(project, confirm_stack, db_path=db_path):
                    git_commit_paths(repo, stack, f"factory: project stack {project_id}")
            return InterviewOutcome(project_id, True, written[0], len(answers))
        if verdict is False:
            return InterviewOutcome(project_id, False, None, len(answers))
        record("correction", "Operator correction to the brief", [], verdict, False)
        wants_done = False


def import_answers(
    project_ref: str, answers: list[dict[str, Any]], *, db_path: Path
) -> InterviewOutcome:
    """Record an interview held elsewhere (the /factory-intake skill) and write its brief.

    The operator approved the brief in that conversation, so this writes it — but
    only when every required topic is answered; otherwise nothing is recorded.
    """
    try:
        imported = TypeAdapter(list[ImportedAnswer]).validate_python(answers)
    except ValidationError as exc:
        raise RunError(f"Imported answers are malformed; nothing was recorded.\n{exc}") from exc
    project = get_project(db_path, project_ref)
    project_id = project["id"]
    with get_db(db_path) as conn:
        existing = list_answers(conn, project_id)
    if missing := uncovered_topics([*existing, *(a.model_dump() for a in imported)]):
        raise RunError(
            "Imported answers leave required topics uncovered: "
            f"{', '.join(missing)}. Nothing was recorded."
        )
    with get_db(db_path) as conn:
        for a in imported:
            add_answer(conn, project_id, topic=a.topic, question=a.question, options=a.options,
                       answer=a.answer, assumed=a.assumed)
        answers_now = list_answers(conn, project_id)
    repo = Path(project["repo_path"])
    written = write_brief(repo, project.get("name") or project_id, answers_now)
    git_commit_paths(repo, written, f"factory: product brief {project_id} (imported)")
    return InterviewOutcome(project_id, True, written[0], len(answers_now))


def build_story_prompt(
    project: dict[str, Any], brief: str, request: str, settled: list[StoryClarification]
) -> str:
    remaining = MAX_STORY_INTERVIEW_QUESTIONS - len(settled)
    # Numbered so a follow-up can name what it settles (`follow_up_of`).
    so_far = "\n".join(
        f"{n}. {c.question} — {c.answer}"
        + (f" [UNDECIDED: the operator was not sure. Ask again with a concrete example and"
           f" set follow_up_of={n}]" if c.undecided and not c.superseded else "")
        for n, c in enumerate(settled, 1)
    ) or "(nothing asked yet)"
    return "\n\n".join([
        "# Mode: story",
        f"# Product: {project.get('name') or project['id']}",
        f"## Approved brief\n\n{brief.rstrip()}",
        f"## Story request\n\n{request}",
        f"## Clarifications so far\n\n{so_far}",
        f"You may ask at most {remaining} more question(s) in total, "
        f"and at most {MAX_QUESTIONS_PER_TURN} in this turn.",
    ]) + "\n"


def run_story_interview(project_ref: str, request: str, *, db_path: Path, ask: Ask) -> str:
    """Ask only what the brief leaves open for THIS story; the request plus the answers.

    The answers belong to the story, so they are appended to its request and never
    stored as project answers (they would leak into the brief).
    """
    project = get_project(db_path, project_ref)
    brief = load_brief(Path(project["repo_path"]))
    if not brief:
        raise RunError(f"Project '{project_ref}' has no approved product brief.")
    settled: list[StoryClarification] = []
    questions: list[InterviewQuestion] = []  # parallel to `settled`, for the re-ask below
    asked = 0
    while asked < MAX_STORY_INTERVIEW_QUESTIONS:
        turn = _ask_agent(project, build_story_prompt(project, brief, request, settled),
                          InterviewTurn, db_path=db_path,
                          resume="Run the story again, or pass --no-interview.")
        if turn.done or not turn.questions:
            break
        room = min(MAX_QUESTIONS_PER_TURN, MAX_STORY_INTERVIEW_QUESTIONS - asked)
        before = len(settled)
        for question in turn.questions[:room]:
            raw = ask(question, [])
            if raw is None:  # the operator is done: what was settled stands
                return with_clarifications(request, settled)
            asked += 1
            answer, assumed = resolve_answer(question, raw)
            if not answer:
                continue
            undecided = not assumed and is_undecided(raw)
            settled.append(StoryClarification(question.question, answer, assumed, undecided))
            questions.append(question)
            target = question.follow_up_of
            if target and 1 <= target < len(settled) and settled[target - 1].undecided:
                # The follow-up is now the live question (decided or still open): the
                # unsure line it follows up drops out, so the doubt is asked about once.
                settled[target - 1] = settled[target - 1]._replace(superseded=True)
        if len(settled) == before:
            break  # nothing answered: asking again would repeat the same paid call
    # An unsure answer is not a decision. What the agent did not follow up is asked once
    # more here (no model call); still unsure, it becomes an assumption, said as one.
    for i, c in enumerate(settled):
        if not c.undecided or c.superseded:
            continue
        again = questions[i].model_copy(
            update={"question": f"You were not sure about this one. {c.question}"})
        raw = ask(again, [])
        if raw is None:
            break  # the operator is done: what is still open stays marked UNDECIDED
        answer, assumed = resolve_answer(questions[i], raw)
        if not answer or (not assumed and is_undecided(raw)):
            answer, assumed = resolve_answer(questions[i], "you decide")
        settled[i] = StoryClarification(c.question, answer, assumed)
    return with_clarifications(request, settled)
