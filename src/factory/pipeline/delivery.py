"""Release = merged PR (operator decision, 2026-10-02).

A live project run builds on `factory/<story>` (`runs.service` cuts it). At
Checkpoint 3 `open_release_pr` offers that branch for review: a real pull request
on GitHub when the product repo's `origin` is there (pushed, with the release notes
as its description), else the branch itself, to be merged locally. The operator's
approval runs `merge_release`; the story counts as released only once the merge
landed. A replay works in a scratch clone and never pushes or opens anything.
"""

from __future__ import annotations

from pathlib import Path

from factory.adapters import github
from factory.evidence.artifacts import work_dir_for
from factory.pipeline.agent_calls import db_conn
from factory.pipeline.state import PipelineState
from factory.state.db import get_run, set_pr_url
from factory.workspace.git import github_remote, merge_story_branch, push_branch


def _run_row(state: PipelineState) -> dict:
    conn = db_conn(state)
    try:
        return get_run(conn, state["run_id"]) or {}
    finally:
        conn.close()


def _pr_body(state: PipelineState) -> str:
    notes = work_dir_for(Path(state["project_dir"]), state["story_id"]) / "RELEASE.md"
    try:
        body = notes.read_text(encoding="utf-8")
    except OSError:
        body = "(no release notes were written)"
    return (
        f"{body}\n\n---\nOpened by the factory at Checkpoint 3. The trust package and "
        f"the run's trail are in `docs/releases/` and `docs/work/{state['story_id']}/`. "
        "Approving the release in the factory merges this pull request."
    )


def open_release_pr(state: PipelineState) -> tuple[str, str | None]:
    """(a note for the operator, a gap or None) — offer the story branch for review."""
    run = _run_row(state)
    branch, target = run.get("story_branch"), run.get("target_branch")
    if not branch or not state.get("project_dir"):
        return "", None
    repo = Path(state.get("opencode_cwd") or state["project_dir"])
    remote = None if state.get("replay_run_id") else github_remote(repo)
    if remote is None:
        return (f"Approving merges branch {branch} into {target} (this repository has no "
                "GitHub remote, so the branch is the pull request)."), None
    pushed, detail = push_branch(repo, branch)
    if not pushed:
        return "", f"The story branch {branch} could not be pushed to GitHub: {detail}"
    title = f"{state['story_id']}: {(state.get('spec') or {}).get('title') or 'release'}"
    url, error = github.open_pull_request(repo, branch, target, title, _pr_body(state))
    if error or not url:
        return "", f"The pull request could not be opened: {error or 'no URL returned'}"
    conn = db_conn(state)
    try:
        set_pr_url(conn, state["run_id"], url)
        conn.commit()
    finally:
        conn.close()
    return f"Pull request: {url} ({branch} → {target}). Approving merges it.", None


def merge_release(state: PipelineState) -> tuple[bool, str]:
    """Merge the approved story: its GitHub PR, else its branch locally."""
    run = _run_row(state)
    branch, target, url = run.get("story_branch"), run.get("target_branch"), run.get("pr_url")
    if not branch:
        return True, "no story branch: nothing to merge (not a project run)"
    repo = Path(state.get("opencode_cwd") or state.get("project_dir") or ".")
    if url and not state.get("replay_run_id"):
        return github.merge_pull_request(repo, url)
    return merge_story_branch(repo, branch, target or "main",
                              f"factory: merge {state['story_id']} ({branch})")
