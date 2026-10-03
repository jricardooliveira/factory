"""Where a project stands, and the one command that moves it on (pure).

Define (the brief) → Plan (the backlog) → Build (story runs) → Release (the
operator's Checkpoint-3 approval). `factory status` and the board render this so
the operator never has to reconstruct the state from runs, files and the DB.
"""

from __future__ import annotations

from dataclasses import dataclass

# A run in one of these still needs something to happen.
_OPEN = ("waiting_human", "running", "failed", "blocked")


@dataclass(frozen=True)
class RunFact:
    id: int
    status: str  # running | waiting_human | completed | failed | blocked | archived
    stage: str
    title: str
    retryable: bool = False  # has an answered checkpoint `factory retry` can re-drive


@dataclass(frozen=True)
class StoryFact:
    position: int
    title: str
    status: str  # backlog row: approved | started | dropped
    run: RunFact | None = None


@dataclass(frozen=True)
class ProjectFacts:
    slug: str
    has_brief: bool
    answers: int
    assumptions: int
    has_stack: bool
    backlog: tuple[StoryFact, ...]
    runs: tuple[RunFact, ...]  # the project's runs, newest first
    intake_usd: float
    stories_usd: float


@dataclass(frozen=True)
class Phase:
    name: str
    state: str  # done | now | todo
    detail: str


@dataclass(frozen=True)
class NextStep:
    command: str
    why: str
    alternative: str = ""


def _released(facts: ProjectFacts) -> int:
    return sum(1 for s in facts.backlog if s.run and s.run.status == "completed")


def _to_do(facts: ProjectFacts) -> list[StoryFact]:
    return [s for s in facts.backlog if s.status == "approved"]


def lifecycle(facts: ProjectFacts) -> list[Phase]:
    stories = [s for s in facts.backlog if s.status != "dropped"]
    released = _released(facts)
    open_runs = [r for r in facts.runs if r.status in _OPEN]
    define = (
        f"brief approved ({facts.answers} answers, {facts.assumptions} assumption"
        f"{'' if facts.assumptions == 1 else 's'}"
        f"{', stack confirmed' if facts.has_stack else ''})"
        if facts.has_brief else
        f"interview in progress ({facts.answers} answers)" if facts.answers else "no brief yet"
    )
    plan = (f"{len(stories)} stories: {released} released, "
            f"{len(stories) - released - len(_to_do(facts))} in progress, "
            f"{len(_to_do(facts))} to do") if stories else "no backlog yet"
    build = (", ".join(f"run #{r.id} {r.status.replace('_', ' ')} at {r.stage}"
                       for r in open_runs) or "nothing running")
    release = f"{released} of {len(stories)} released" if stories else "0 released"

    def state(done: bool, started: bool) -> str:
        return "done" if done else "now" if started else "todo"

    all_released = bool(stories) and released == len(stories)
    phases = [
        Phase("Define", state(facts.has_brief, True), define),
        Phase("Plan", state(bool(stories), facts.has_brief), plan),
        Phase("Build", state(all_released, bool(stories) and not all_released), build),
        Phase("Release", state(all_released, False), release),
    ]
    return phases


def next_step(facts: ProjectFacts) -> NextStep:
    slug = facts.slug
    if not facts.has_brief:
        if facts.answers:
            return NextStep(f"factory interview {slug}",
                            f"the interview is paused with {facts.answers} answers saved")
        return NextStep(f"factory interview {slug}", "define the product before any story")
    # Runs come before the backlog: one live run per project, and a parked one waits on you.
    for run in facts.runs:
        if run.status == "waiting_human":
            return NextStep(f"factory approve {run.id}",
                            f"run #{run.id} ({run.title}) waits for your sign-off at {run.stage}",
                            f'factory reject {run.id} "what to change"')
        if run.status == "running":
            return NextStep("factory board", f"run #{run.id} is working: {run.stage}")
        if run.status in ("failed", "blocked"):
            # Dismissing a failed run returns its story to the backlog for `next`.
            again = f"factory dismiss {run.id} then factory next {slug}"
            return NextStep(f"factory review {run.id}",
                            f"run #{run.id} {run.status} at {run.stage}",
                            f"factory retry {run.id}  (or {again})" if run.retryable
                            else f"{again}  (the story goes back to the backlog)")
    if not facts.backlog:
        return NextStep(f"factory backlog {slug}", "the brief is approved; plan the stories")
    if todo := _to_do(facts):
        return NextStep(f"factory next {slug}", f"next story: {todo[0].title}")
    return NextStep(f'factory interview {slug} --amend "what changed"',
                    "every backlog story has run; amend the brief to plan more",
                    f'factory run --project {slug} "a new request"')
