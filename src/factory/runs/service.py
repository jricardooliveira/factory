"""Run orchestration as a service: run, replay, resume, retry.

This is the code the CLI used to carry inline. It owns every DB write that
starts, reopens or re-drives a run, picks the graph to stream, and reports each
step to an `on_event` callback (see `runs.events`). It never prints: the CLI
renders the events live, and the TUI passes no callback, which is how the TUI
stays silent without the console-swapping hack it used to need.

Refusals and dead ends raise `RunError` with the operator-facing message;
whether that is a red line, a non-zero exit or a notice in the board is the
interface's call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factory.adapters.notify import notify
from factory.pipeline import (
    PipelineState,
    compile_architect_resume_pipeline,
    compile_coder_only_pipeline,
    compile_pipeline,
    compile_spec_resume_pipeline,
    resume_entry_for,
)
from factory.runs.context import (
    build_resume_context,
    decision_from_response,
    load_project_spec_text,
    park_unresumable,
)
from factory.runs.events import (
    NodeCompleted,
    OnEvent,
    ResumeEntered,
    RetryStarted,
    RunError,
    RunFinished,
    RunOutcome,
    RunStarted,
    _ignore,
)
from factory.state.db import (
    create_story,
    get_answered_human_gate,
    get_db,
    get_pending_human_gate,
    init_db,
    respond_to_gate,
    start_run,
)
from factory.workspace.git import git_head
from factory.workspace.projects import get_project


def run_pipeline(
    request: str,
    opencode_cwd: str | None = None,
    project_spec_text: str | None = None,
    *,
    db_path: Path,
    project_id: str | None = None,
    replay_run_id: int | None = None,
    on_event: OnEvent | None = None,
) -> RunOutcome:
    """Start a run (or replay one on frozen outputs) and stream it to the end."""
    emit = on_event or _ignore

    # Init DB; create a new story (fresh run) or reuse the replayed run's story
    init_db(db_path)
    with get_db(db_path) as conn:
        if replay_run_id:
            orig = conn.execute(
                "SELECT story_id FROM pipeline_runs WHERE id = ?", (replay_run_id,)
            ).fetchone()
            story_id = orig["story_id"]
        else:
            # Auto-increment story ID
            row = conn.execute("SELECT COUNT(*) as c FROM stories").fetchone()
            n = row["c"] + 1
            story_id = f"US-{n:04d}"
            create_story(conn, story_id, "Pending", request, project_id=project_id)
        # Pin the target repo's HEAD as THIS run's baseline, so the trust package
        # can measure this run's change set from git rather than lumping in every
        # factory commit ever made to the repo.
        base_commit = git_head(Path(opencode_cwd or Path.cwd()))
        run_id = start_run(
            conn, story_id, project_id=project_id, base_commit=base_commit
        )

    emit(RunStarted(run_id, story_id, request, project_spec_text, replay_run_id))

    pipeline = compile_pipeline()
    initial_state: PipelineState = {
        "request": request,
        "story_id": story_id,
        "run_id": run_id,
        "db_path": str(db_path),
        "opencode_cwd": opencode_cwd or str(Path.cwd()),
    }
    if project_spec_text:
        initial_state["project_spec"] = project_spec_text
    # Project runs get a project_dir so ADRs + decision memory work. The project
    # directory IS the repository (evidence under its docs/), so it is the cwd.
    if project_id and opencode_cwd:
        initial_state["project_dir"] = str(opencode_cwd)
    if replay_run_id:
        initial_state["replay_run_id"] = replay_run_id

    final_state: dict[str, Any] = dict(initial_state)
    for event in pipeline.stream(initial_state):
        for node_name, node_output in event.items():
            final_state.update(node_output)
            emit(NodeCompleted(node_name, node_output))

    return _finish(run_id, story_id, final_state, db_path, emit)


def run_project_pipeline(
    project_ref: str,
    request: str,
    *,
    db_path: Path,
    on_event: OnEvent | None = None,
) -> RunOutcome:
    """Run the pipeline for a registered project (ValueError if it is unknown)."""
    project = get_project(db_path, project_ref)
    return run_pipeline(
        request,
        opencode_cwd=project["repo_path"],
        project_spec_text=load_project_spec_text(project),
        db_path=db_path,
        project_id=project["id"],
        on_event=on_event,
    )


def replay_run(run_id: int, *, db_path: Path, on_event: OnEvent | None = None) -> RunOutcome:
    """Re-run the orchestration against a past run's frozen agent outputs."""
    init_db(db_path)
    with get_db(db_path) as conn:
        orig = conn.execute(
            "SELECT pr.*, s.request FROM pipeline_runs pr "
            "JOIN stories s ON pr.story_id = s.id WHERE pr.id = ?",
            (run_id,),
        ).fetchone()
        if not orig:
            raise RunError(f"No run found with id #{run_id}")
        orig = dict(orig)

    project_spec_text = None
    opencode_cwd = None
    if orig.get("project_id"):
        project = get_project(db_path, orig["project_id"])
        if project:
            opencode_cwd = project["repo_path"]
            project_spec_text = load_project_spec_text(project)

    return run_pipeline(
        orig["request"],
        opencode_cwd=opencode_cwd,
        project_spec_text=project_spec_text,
        db_path=db_path,
        project_id=orig.get("project_id"),
        replay_run_id=run_id,
        on_event=on_event,
    )


def resume_run(
    run_id: int,
    action: str,
    reason: str | None = None,
    *,
    db_path: Path,
    on_event: OnEvent | None = None,
) -> RunOutcome:
    """Resume a paused pipeline run after human approval/rejection."""
    emit = on_event or _ignore
    init_db(db_path)
    with get_db(db_path) as conn:
        run = conn.execute(
            "SELECT pr.*, s.request FROM pipeline_runs pr "
            "JOIN stories s ON pr.story_id = s.id WHERE pr.id = ?",
            (run_id,),
        ).fetchone()
        if not run:
            raise RunError(f"No run found with id #{run_id}")
        run = dict(run)

        if run["status"] != "waiting_human":
            raise RunError(
                f"Run #{run_id} is not waiting for human approval (status: {run['status']})"
            )

        pending = get_pending_human_gate(conn, run_id)
        if not pending:
            raise RunError(f"No pending human gate found for run #{run_id}")

        # Record the human decision on the pending gate and reopen the run.
        decision = reason or (
            "Rejected by human reviewer" if action == "reject" else "Approved by human reviewer"
        )
        verdict_word = "REJECTED" if action == "reject" else "APPROVED"
        respond_to_gate(conn, pending["id"], f"{verdict_word}: {decision}")
        conn.execute("UPDATE pipeline_runs SET status = 'running' WHERE id = ?", (run_id,))
        conn.commit()

    # Reconstruct context from the LATEST output of each stage — see
    # build_resume_context for why "latest" is load-bearing.
    with get_db(db_path) as conn:
        spec_parsed, arch_parsed = build_resume_context(conn, run_id)
    if spec_parsed is None:
        _unresumable(run_id, "missing spec log", db_path)

    opencode_cwd = str(Path.cwd())
    project_spec_text = None
    project_dir = None
    if run.get("project_id"):
        project = get_project(db_path, run["project_id"])
        opencode_cwd = project["repo_path"]
        project_spec_text = load_project_spec_text(project)
        project_dir = opencode_cwd

    state: dict[str, Any] = {
        "request": run["request"],
        "story_id": run["story_id"],
        "run_id": run_id,
        "db_path": str(db_path),
        "opencode_cwd": opencode_cwd,
        "spec": spec_parsed,
        "status": "running",
    }
    if project_spec_text:
        state["project_spec"] = project_spec_text
    if project_dir:
        state["project_dir"] = project_dir

    # Which stage to re-enter depends on WHICH checkpoint parked the run — a
    # Checkpoint-1 park has no architecture yet, so the old always-resume-at-
    # architecture/coder logic had no way back into the line for it.
    entry = resume_entry_for(pending.get("gate_name"), action)
    if entry == "spec":
        pipeline = compile_spec_resume_pipeline()
        state.update({"prior_findings": [decision], "triggered_by": "spec-rejected"})
    elif entry == "architect":
        pipeline = compile_architect_resume_pipeline()
        if action == "reject":
            state.update({
                "prior_findings": [decision],
                "triggered_by": "architecture-rejected",
            })
    else:
        if arch_parsed is None:
            _unresumable(run_id, "missing architect log", db_path)
        pipeline = compile_coder_only_pipeline()
        state["architect"] = arch_parsed
    emit(ResumeEntered(run_id, entry, action, decision))

    final_state = state
    for event in pipeline.stream(state, stream_mode="updates"):
        for node_name, node_output in event.items():
            final_state = {**final_state, **node_output}
            emit(NodeCompleted(node_name, node_output))

    return _finish(run_id, run["story_id"], final_state, db_path, emit)


def retry_run(run_id: int, *, db_path: Path, on_event: OnEvent | None = None) -> RunOutcome:
    """Re-drive a run that died after the operator answered a checkpoint.

    A transient provider error between `factory reject` and the re-run left the
    decision stranded: the run was no longer `waiting_human`, so approve/reject
    refused it, and the recorded answer had no reader. This puts the run back
    where it was and replays the SAME decision — no re-interruption.
    """
    emit = on_event or _ignore
    init_db(db_path)
    with get_db(db_path) as conn:
        run = conn.execute(
            "SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not run:
            raise RunError(f"No run found with id #{run_id}")
        if run["status"] not in ("blocked", "failed"):
            raise RunError(
                f"Run #{run_id} is '{run['status']}', not blocked/failed. "
                f"Use `factory approve/reject` for a run awaiting review."
            )
        gate = get_answered_human_gate(conn, run_id)
        if not gate:
            raise RunError(
                f"Run #{run_id} has no answered checkpoint to retry from. "
                f"Start a fresh run instead."
            )
        action, feedback = decision_from_response(gate["human_response"])
        # Re-open the decision so resume_run can consume it exactly as before.
        conn.execute(
            "UPDATE gate_results SET human_response = NULL WHERE id = ?", (gate["id"],)
        )
        conn.execute(
            "UPDATE pipeline_runs SET status = 'waiting_human', error = NULL WHERE id = ?",
            (run_id,),
        )
        conn.commit()

    emit(RetryStarted(run_id, gate["gate_name"], action, feedback))
    return resume_run(run_id, action, reason=feedback or None, db_path=db_path, on_event=on_event)


def _unresumable(run_id: int, reason: str, db_path: Path) -> None:
    """Park the run as blocked (never leave it 'running'), then refuse."""
    with get_db(db_path) as conn:
        park_unresumable(conn, run_id, reason)
    raise RunError(f"Cannot resume: {reason}")


def _finish(
    run_id: int, story_id: str, final_state: dict[str, Any], db_path: Path, emit: OnEvent
) -> RunOutcome:
    status = final_state.get("status", "unknown")
    human_qs = (
        final_state.get("gate_2", {}).get("human_questions")
        if status == "waiting_human"
        else None
    )
    outcome = RunOutcome(
        run_id=run_id,
        story_id=story_id,
        status=status,
        error=final_state.get("error"),
        current_stage=final_state.get("current_stage"),
        db_path=db_path,
        human_questions=human_qs,
        final_state=final_state,
    )
    emit(RunFinished(outcome))
    _notify_if_parked(run_id, story_id, status, outcome.current_stage)
    return outcome


def _notify_if_parked(run_id: int, story_id: str, status: str, stage: str | None = None) -> None:
    """Ping the operator when a run parks for review or escalates a failure."""
    if status == "waiting_human":
        notify(
            "Factory: needs your review",
            f"Run #{run_id} {story_id} parked at {stage or 'a checkpoint'} - factory queue",
        )
    elif status in ("failed", "blocked"):
        notify(
            "Factory: run needs attention",
            f"Run #{run_id} {story_id} {status} - factory queue",
        )
