"""The story backlog: from the approved brief to an ordered list of small stories.

The backlog-agent only PROPOSES; the operator approves, rejects or sends feedback
(`review`), and only an approved proposal is stored. Stories already started are
fixed: a new proposal replaces only the unstarted remainder. The operator starts
each story with `factory next` — nothing here chains runs. Like the rest of `runs`,
nothing here prints.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factory.adapters.opencode import run_agent
from factory.agent_config.tiers import resolve_model
from factory.domain.agent_output import parse_agent_json
from factory.domain.backlog import BacklogOutput, BacklogStory
from factory.domain.gates import MAX_BACKLOG_REVISIONS
from factory.evidence.backlog import render_backlog, write_backlog
from factory.evidence.brief import load_brief
from factory.runs.events import RunError
from factory.state import backlog as rows
from factory.state.db import get_db
from factory.state.interviews import log_turn
from factory.workspace.git import git_commit_paths
from factory.workspace.projects import get_project

AGENT = "backlog-agent"

# proposed stories -> True approve / False stop for now / str = feedback, regenerate.
Review = Callable[[list[BacklogStory]], bool | str]


@dataclass(frozen=True)
class BacklogOutcome:
    approved: bool
    stories: int


def build_backlog_prompt(
    project: dict[str, Any], brief: str, current: list[dict[str, Any]], feedback: list[str]
) -> str:
    name = project.get("name") or project["id"]
    parts = [brief.rstrip()]
    if current:
        # Reuses BACKLOG.md's rendering, so the agent sees exactly what the operator does.
        listing = render_backlog(name, current).split("\n", 1)[1].strip()
        parts.append(
            "## Current backlog\n\nStories marked `started` are FIXED: do not propose them "
            f"again. Your proposal replaces every other story.\n\n{listing}"
        )
    if feedback:
        parts.append(
            "## Operator feedback on your earlier proposals (oldest first)\n\n"
            + "\n".join(f"- {f}" for f in feedback)
        )
    return "\n\n".join(parts) + "\n"


def _propose(project: dict[str, Any], prompt: str, *, db_path: Path) -> list[BacklogStory]:
    model, _tier = resolve_model(AGENT)
    result = run_agent(AGENT, prompt, cwd=project["repo_path"], model=model)
    # Logged before it is judged: a call that fails below must still be on record.
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
    if not result.success:
        raise RunError(f"{AGENT} call failed (exit {result.returncode}). Nothing was saved.")
    try:
        return BacklogOutput.model_validate(parse_agent_json(result.output)).stories
    except ValidationError as exc:
        raise RunError(f"{AGENT} returned an unusable response. Nothing was saved.") from exc


def _write_and_commit(project: dict[str, Any], *, db_path: Path) -> None:
    repo = Path(project["repo_path"])
    with get_db(db_path) as conn:
        current = rows.list_backlog(conn, project["id"])
    written = write_backlog(repo, project.get("name") or project["id"], current)
    git_commit_paths(repo, [written], f"factory: backlog {project['id']}")


def propose_backlog(project_ref: str, *, db_path: Path, review: Review) -> BacklogOutcome:
    project = get_project(db_path, project_ref)
    brief = load_brief(Path(project["repo_path"]))
    if not brief:
        raise RunError(
            f"Project '{project_ref}' has no approved product brief. "
            f"Run `factory interview {project_ref}` first."
        )
    with get_db(db_path) as conn:
        current = rows.list_backlog(conn, project["id"])

    feedback: list[str] = []
    while True:
        stories = _propose(project, build_backlog_prompt(project, brief, current, feedback),
                           db_path=db_path)
        verdict = review(stories)
        if verdict is True:
            with get_db(db_path) as conn:
                rows.replace_unstarted(conn, project["id"], stories)
            _write_and_commit(project, db_path=db_path)
            return BacklogOutcome(approved=True, stories=len(stories))
        if verdict is False or len(feedback) >= MAX_BACKLOG_REVISIONS:
            return BacklogOutcome(approved=False, stories=0)
        feedback.append(verdict)


def next_story(project_ref: str, *, db_path: Path) -> dict[str, Any] | None:
    """The first approved, unstarted story, or None when the backlog is used up."""
    project = get_project(db_path, project_ref)
    with get_db(db_path) as conn:
        return rows.next_approved(conn, project["id"])


def mark_started(row_id: int, *, story_id: str | None, run_id: int | None, db_path: Path) -> None:
    with get_db(db_path) as conn:
        rows.mark_started(conn, row_id, story_id=story_id, run_id=run_id)
        row = rows.get_backlog_row(conn, row_id)
    if row is None:
        raise RunError(f"No backlog story #{row_id}")
    _write_and_commit(get_project(db_path, row["project_id"]), db_path=db_path)
