"""The board's pure derivations: story states, stage strips, inbox order, the next start.

The rules come from the operator's design handoff (`design_handoff_factory_board/`,
`screens.js`); the records come from `runs.board`. Nothing here reads a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from factory.domain.workflow import StoryPlan, conflicts

# Pipeline stage labels (evidence.progress) folded into the four the operator reads.
_STAGE_OF = {"Spec": 0, "Gate 1": 0, "Architect": 1, "Gate 2": 1,
             "Coder": 2, "Build": 2, "Tester": 3, "Test": 3}


def stage_strip(progress: Iterable[tuple[str, str]]) -> tuple[str, str, str, str]:
    """spec/design/code/test as done | run | wait | fail | todo, from per-stage statuses
    (done | current | waiting | failed | pending)."""
    groups: list[list[str]] = [[], [], [], []]
    for label, status in progress:
        if label in _STAGE_OF:
            groups[_STAGE_OF[label]].append(status)

    def fold(statuses: list[str]) -> str:
        if "failed" in statuses:
            return "fail"
        if "waiting" in statuses:
            return "wait"
        if "current" in statuses:
            return "run"
        return "done" if statuses and all(s == "done" for s in statuses) else "todo"

    return tuple(fold(g) for g in groups)  # type: ignore[return-value]


@dataclass(frozen=True)
class StoryFacts:
    """What is recorded about one backlog story, from the newest record that applies."""

    session: str = ""  # refinement session status, "" when never refined
    questions: int = 0  # pending questions of that session
    uncertainties: int = 0
    plan_ready: bool = False
    build_queued: bool = False
    run: str = ""  # its pipeline run's status, "" when none
    run_stage: str = ""
    stop_requested: bool = False
    awaiting_merge: bool = False
    refine_failed: bool = False  # its newest refinement step failed and was not retried


def story_state(f: StoryFacts) -> tuple[str, str]:
    """(state, meta): state ∈ draft needs notready refining ready working stopped release
    merging done — the design's story states, newest record first."""
    if f.run == "completed":
        return "done", ""
    if f.awaiting_merge:
        return "release", "waiting for you"
    if f.run == "waiting_human":
        if "release" in f.run_stage:
            return "release", "waiting for you"
        return "working", "waiting for you"
    if f.run == "running":
        return "working", "stopping after this step" if f.stop_requested else ""
    if f.run in ("failed", "blocked"):
        return "notready", "last run failed"
    if f.build_queued:
        return "working", "starting"
    if f.refine_failed:
        return "notready", "refinement failed"
    if f.session == "needs_input":
        return "needs", f"{f.questions} question{'' if f.questions == 1 else 's'}"
    if f.session == "refining":
        return "refining", ""
    if f.session == "blocked":
        n = f.uncertainties
        return "notready", f"{n} uncertaint{'y' if n == 1 else 'ies'}" if n else "not ready"
    if f.session == "ready" or f.plan_ready:
        return "ready", ""
    return "draft", ""


_GROUP = {"fail": 0, "ckpt": 1, "release": 1, "backlog": 1, "brief": 1, "questions": 2}
GROUP_TITLES = ("FAILED", "APPROVE", "ANSWER")


def inbox_group(kind: str) -> int:
    """Needs you order: failures first, then approvals, then questions."""
    return _GROUP[kind]


def age(stamp: str, now: datetime) -> str:
    seconds = max(0, int((now - datetime.fromisoformat(stamp)).total_seconds()))
    if seconds < 60:
        return "just now" if seconds < 5 else f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _shared_file(a: StoryPlan, b: StoryPlan) -> str:
    reason = next((r for r in conflicts(a, b) if r.startswith("Shared file")), "")
    return reason.split(": ", 1)[1].split(" / ", 1)[0] if reason else ""


def pick_batch(ready: list[StoryPlan], *, limit: int) -> tuple[list[StoryPlan], str]:
    """A greedy pick of ready stories that change different things, up to `limit`, and
    why the first one left out waits."""
    picked: list[StoryPlan] = []
    for plan in ready:
        if len(picked) < limit and not any(conflicts(plan, p) for p in picked):
            picked.append(plan)
    rest = [p for p in ready if p not in picked]
    if not rest:
        return picked, ""
    waiting = rest[0]
    clash = next((p for p in picked if conflicts(waiting, p)), None)
    if clash is not None:
        shared = _shared_file(waiting, clash) or "the same part"
        return picked, f"#{waiting.backlog_id} waits: it changes {shared} like #{clash.backlog_id}."
    return picked, f"#{waiting.backlog_id} waits: {limit} run at a time."


@dataclass(frozen=True)
class NextStart:
    action: str  # interview | pause | batch | backlog | "" (nothing to press)
    title: str
    reason: str = ""
    button: str = ""
    key: str = ""


def next_start(*, brief: bool, intake_open: bool, paused: bool, ready: list[StoryPlan],
               stories: int, backlog_open: bool, question_subject: str, project: str = "",
               limit: int = 2) -> NextStart:
    """Exactly ONE suggestion for what can start, in the design's priority order."""
    if not brief:
        if intake_open:
            return NextStart("", f"Defining {project or 'the product'}: answer the interview "
                                 "in Needs you.")
        return NextStart("interview", "Start the interview",
                         f"a few rounds of questions define {project or 'the product'}",
                         "Start interview", "i")
    if paused:
        return NextStart("pause", "New starts are paused.", "Running work continues.",
                         "Resume new starts", "P")
    if ready:
        picked, why = pick_batch(ready, limit=limit)
        names = " and ".join(f"#{p.backlog_id}" for p in picked)
        verb = "can run together" if len(picked) > 1 else "can start"
        return NextStart("batch", f"Propose a batch · {len(ready)} ready · {names} {verb}",
                         why, "Propose batch", "b")
    if not stories and not backlog_open:
        return NextStart("backlog", "Propose a backlog", "turns the brief into stories",
                         "Propose backlog", "b")
    reason = f"Answering {question_subject} would make it ready." if question_subject else ""
    return NextStart("", "Nothing can start yet.", reason)
