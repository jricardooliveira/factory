"""`factory status`: where each project stands and the next command (read-only).

The facts come from the DB and the project repo; `domain.lifecycle` decides what
they mean, so the CLI and the board show the same answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from factory.agent_config import tiers
from factory.domain.budget import story_spend
from factory.domain.lifecycle import (
    NextStep,
    Phase,
    ProjectFacts,
    RunFact,
    StoryFact,
    lifecycle,
    next_step,
)
from factory.evidence.brief import brief_path
from factory.state.backlog import list_backlog
from factory.state.db import (
    get_answered_human_gate,
    get_db,
    init_db,
    project_runs,
    usage_rows,
)
from factory.state.interviews import list_answers, turn_usage_rows
from factory.workspace.projects import get_project, list_projects


@dataclass(frozen=True)
class ProjectStatus:
    name: str
    project_id: str
    repo_path: str
    facts: ProjectFacts
    phases: list[Phase]
    next: NextStep


def _status(project: dict, db_path: Path) -> ProjectStatus:
    prices = tiers.config().prices
    with get_db(db_path) as conn:
        answers = list_answers(conn, project["id"])
        backlog = list_backlog(conn, project["id"])
        run_rows = project_runs(conn, project["id"])
        intake = story_spend(turn_usage_rows(conn, project["id"]), prices).estimated_usd
        stories = story_spend(usage_rows(conn, project_id=project["id"]), prices).estimated_usd
        # The same test `retry_run` applies, so status never suggests a retry it refuses.
        runs = tuple(RunFact(r["id"], r["status"], r["current_stage"], r["story_title"],
                             retryable=get_answered_human_gate(conn, r["id"]) is not None,
                             archived_from=r["archived_from"] or "")
                     for r in run_rows)
    by_id = {r.id: r for r in runs}
    facts = ProjectFacts(
        slug=project["slug"],
        has_brief=brief_path(Path(project["repo_path"])).is_file(),
        answers=len(answers),
        assumptions=sum(1 for a in answers if a.get("assumed")),
        has_stack=bool(project.get("spec_path")),
        backlog=tuple(StoryFact(b["position"], b["title"], b["status"], by_id.get(b["run_id"]))
                      for b in backlog),
        runs=runs,
        intake_usd=round(intake, 4),
        stories_usd=round(stories, 4),
    )
    return ProjectStatus(project.get("name") or project["slug"], project["id"],
                         project["repo_path"], facts, lifecycle(facts), next_step(facts))


def project_status(project_ref: str, *, db_path: Path) -> ProjectStatus:
    init_db(db_path)
    return _status(get_project(db_path, project_ref), db_path)


def all_project_status(*, db_path: Path) -> list[ProjectStatus]:
    init_db(db_path)
    return [_status(p, db_path) for p in list_projects(db_path)]
