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

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from factory.adapters.notify import notify
from factory.agent_config import tiers
from factory.domain.budget import story_spend
from factory.domain.contracts import SpecOutput
from factory.domain.task_order import order_tasks
from factory.evidence.pipeline_record import write_pipeline_record
from factory.pipeline import (
    PipelineState,
    compile_architect_resume_pipeline,
    compile_coder_only_pipeline,
    compile_pipeline,
    compile_release_pipeline,
    compile_spec_resume_pipeline,
    resume_entry_for,
)
from factory.runs.context import (
    last_boundary_review,
    build_resume_context,
    decision_from_response,
    load_project_spec_text,
    park_unresumable,
)
from factory.runs.events import (
    NodeCompleted,
    NodeStarted,
    OnEvent,
    ResumeEntered,
    RetryStarted,
    RunError,
    RunFinished,
    RunOutcome,
    RunStarted,
    ignore_events,
)
from factory.state.db import (
    agent_logs_after,
    last_agent_log_id,
    live_runs_in_project,
    create_story,
    get_answered_human_gate,
    get_db,
    get_pending_human_gate,
    get_run,
    init_db,
    next_story_id,
    reopen_run,
    requeue_answered_gate,
    respond_to_gate,
    start_run,
)
from factory.verification import tests_enabled
from factory.workspace.git import git_commit_paths, git_head
from factory.workspace.projects import agents_link_problem, get_project
from factory.workspace.sandbox import prepare_replay_sandbox


def run_pipeline(
    request: str,
    opencode_cwd: str | None = None,
    project_spec_text: str | None = None,
    *,
    db_path: Path,
    project_id: str | None = None,
    replay_run_id: int | None = None,
    on_event: OnEvent | None = None,
    notify_operator: bool = True,
    prepare_workdir: Callable[[Path], None] | None = None,
) -> RunOutcome:
    """Start a run (or replay one on frozen outputs) and stream it to the end.

    A replay never works in `opencode_cwd` itself: it gets a scratch clone of it
    (see `workspace.sandbox`), so re-driving a past run cannot touch the product.
    `prepare_workdir` is called on the working directory before the graph runs
    (the scenario matrix seeds its fixture repository this way);
    `notify_operator=False` keeps a run that nobody waits on from pinging anyone.
    """
    emit = on_event or ignore_events
    cwd = Path(opencode_cwd) if opencode_cwd else Path.cwd()

    # Init DB; create a new story (fresh run) or reuse the replayed run's story
    init_db(db_path)
    with get_db(db_path) as conn:
        if replay_run_id:
            orig = get_run(conn, replay_run_id)
            if not orig:
                raise RunError(f"No run found with id #{replay_run_id}")
            story_id = orig["story_id"]
            # The replay starts where the replayed run started (its sandbox is
            # checked out there); never at whatever repo the process sits in.
            base_commit = orig.get("base_commit") or (
                git_head(Path(opencode_cwd)) if opencode_cwd else None
            )
        else:
            if project_id:
                _refuse_if_project_busy(conn, project_id)
            story_id = next_story_id(conn)
            create_story(conn, story_id, "Pending", request, project_id=project_id)
            # Pin the target repo's HEAD as THIS run's baseline, so the trust package
            # can measure this run's change set from git rather than lumping in every
            # factory commit ever made to the repo.
            base_commit = git_head(cwd)
        run_id = start_run(
            conn, story_id, project_id=project_id, base_commit=base_commit,
            replay_of=replay_run_id,
        )

    if replay_run_id:
        cwd = prepare_replay_sandbox(
            run_id, db_path,
            source=Path(opencode_cwd) if opencode_cwd else None,
            commit=base_commit,
        )
    if prepare_workdir is not None:
        prepare_workdir(cwd)

    emit(RunStarted(run_id, story_id, request, project_spec_text, replay_run_id,
                    tests_run=tests_enabled()))

    pipeline = compile_pipeline()
    initial_state: PipelineState = {
        "request": request,
        "story_id": story_id,
        "run_id": run_id,
        "db_path": str(db_path),
        "opencode_cwd": str(cwd),
    }
    if base_commit:
        initial_state["base_commit"] = base_commit
    if project_spec_text:
        initial_state["project_spec"] = project_spec_text
    # Project runs get a project_dir so ADRs + decision memory work. The project
    # directory IS the repository (evidence under its docs/), so it is the cwd.
    if project_id and (opencode_cwd or replay_run_id):
        initial_state["project_dir"] = str(cwd)
    if replay_run_id:
        initial_state["replay_run_id"] = replay_run_id

    final_state = _stream(pipeline, initial_state, run_id, db_path, emit)
    return _finish(run_id, story_id, final_state, db_path, emit, notify_operator)


def _stream(
    pipeline: Any, state: dict[str, Any], run_id: int, db_path: Path, emit: OnEvent
) -> dict[str, Any]:
    """Drive the graph to its end: NodeStarted before each node runs, NodeCompleted
    (with the duration + cost of the agent calls it logged) after. Returns the final state."""
    final_state = dict(state)
    mark = 0
    # "tasks" announces a node before it runs (and again with its result, ignored);
    # "updates" carries the state update it returned.
    for mode, chunk in pipeline.stream(state, stream_mode=["tasks", "updates"]):
        if mode == "tasks":
            if "input" in chunk:
                with get_db(db_path) as conn:
                    mark = last_agent_log_id(conn)
                emit(NodeStarted(chunk["name"], node_detail(chunk["name"], chunk["input"])))
            continue
        for node_name, node_output in chunk.items():
            final_state.update(node_output)
            emit(NodeCompleted(node_name, node_output, *_node_usage(db_path, run_id, mark)))
    return final_state


def node_detail(node: str, state: dict[str, Any]) -> str:
    """Which coder task (and attempt) is about to run; '' for any other node."""
    if node != "coder-agent" or not isinstance(state, dict):
        return ""
    if state.get("remediation"):
        return "remediation pass"
    if not state.get("spec"):
        return ""
    tasks = order_tasks(list(SpecOutput.model_validate(state["spec"]).tasks))
    index = state.get("task_index", 0)
    if index >= len(tasks):
        return ""
    task = tasks[index]
    return (f"task {index + 1}/{len(tasks)} {task.id} {task.title} "
            f"(attempt {state.get('attempt_number', 1)})")


def _node_usage(db_path: Path, run_id: int, mark: int) -> tuple[float | None, float | None]:
    """(seconds, USD) of the agent calls a node logged since `mark`; None = none / unknown."""
    with get_db(db_path) as conn:
        rows = agent_logs_after(conn, run_id, mark)
    if not rows:
        return None, None
    duration = sum(r["duration_secs"] or 0.0 for r in rows) or None  # a replay logs 0.0
    spend = story_spend(rows, tiers.config().prices)
    return duration, (spend.estimated_usd if spend.priced_calls else None)


def run_project_pipeline(
    project_ref: str,
    request: str,
    *,
    db_path: Path,
    on_event: OnEvent | None = None,
) -> RunOutcome:
    """Run the pipeline for a registered project (ValueError if it is unknown)."""
    project = get_project(db_path, project_ref)
    _require_agents_link(Path(project["repo_path"]))
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
        orig = get_run(conn, run_id)
    if not orig:
        raise RunError(f"No run found with id #{run_id}")

    project_spec_text = None
    opencode_cwd = None
    if orig.get("project_id"):
        project = get_project(db_path, orig["project_id"])
        if project:
            # The SOURCE of the replay's scratch clone, never its working directory.
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
    """Resume a paused pipeline run after human approval/rejection.

    A parked REPLAY resumes as a replay: the same frozen outputs, in the same
    scratch clone. Resuming it live would spend tokens and write into the product.
    """
    emit = on_event or ignore_events
    init_db(db_path)
    with get_db(db_path) as conn:
        run = get_run(conn, run_id)
        if not run:
            raise RunError(f"No run found with id #{run_id}")

        if run["status"] != "waiting_human":
            raise RunError(
                f"Run #{run_id} is not waiting for human approval (status: {run['status']})"
            )

        pending = get_pending_human_gate(conn, run_id)
        if not pending:
            raise RunError(f"No pending human gate found for run #{run_id}")
        if run.get("project_id") and not run.get("replay_of"):
            _refuse_if_project_busy(conn, run["project_id"], except_run=run_id)

        # Record the human decision on the pending gate and reopen the run.
        decision = reason or (
            "Rejected by human reviewer" if action == "reject" else "Approved by human reviewer"
        )
        verdict_word = "REJECTED" if action == "reject" else "APPROVED"
        respond_to_gate(conn, pending["id"], f"{verdict_word}: {decision}")
        reopen_run(conn, run_id)
        conn.commit()

    # Reconstruct context from the LATEST output of each stage — see
    # build_resume_context for why "latest" is load-bearing.
    with get_db(db_path) as conn:
        spec_parsed, arch_parsed = build_resume_context(conn, run_id)
    if spec_parsed is None:
        _unresumable(run_id, "missing spec log", db_path)

    state = _resume_state(run, spec_parsed, db_path)

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
    elif entry == "release":
        pipeline = compile_release_pipeline()
    elif entry == "remediation":
        # Checkpoint 3 rejected: every task is built; the operator's words become
        # the findings of a remediation pass, then the tester, then release again.
        if arch_parsed is None:
            _unresumable(run_id, "missing architect log", db_path)
        pipeline = compile_coder_only_pipeline()
        state.update({
            "architect": arch_parsed,
            "remediation": True,
            "prior_findings": [decision],
            "triggered_by": "release-rejected",
            "tasks_completed": [t.get("id") for t in spec_parsed.get("tasks", [])],
            "tester_attempt": 1,
            "attempt_number": 2,  # a rejected release is reasoning work: frontier coder
        })
    else:
        if arch_parsed is None:
            _unresumable(run_id, "missing architect log", db_path)
        pipeline = compile_coder_only_pipeline()
        state["architect"] = arch_parsed
    if state.get("architect"):
        # The approved design's boundary rules travel with it to the coder and tester.
        with get_db(db_path) as conn:
            review = last_boundary_review(conn, run_id)
        if review:
            state.update({"boundary": review, "boundary_status": "reviewed"})
    emit(ResumeEntered(run_id, entry, action, decision))

    final_state = _stream(pipeline, state, run_id, db_path, emit)
    return _finish(run_id, run["story_id"], final_state, db_path, emit)


def _resume_state(run: dict[str, Any], spec: dict, db_path: Path) -> dict[str, Any]:
    """The graph state a resume starts from: where it works, what it knows."""
    run_id = run["id"]
    opencode_cwd = str(Path.cwd())
    project_spec_text = None
    project_dir = None
    project = get_project(db_path, run["project_id"]) if run.get("project_id") else None
    if project:
        opencode_cwd = project["repo_path"]
        project_spec_text = load_project_spec_text(project)
        project_dir = opencode_cwd
    if run.get("replay_of"):
        sandbox = prepare_replay_sandbox(
            run_id, db_path,
            source=Path(project["repo_path"]) if project else None,
            commit=run.get("base_commit"),
        )
        opencode_cwd = str(sandbox)
        project_dir = opencode_cwd if project else None

    state: dict[str, Any] = {
        "request": run["request"],
        "story_id": run["story_id"],
        "run_id": run_id,
        "db_path": str(db_path),
        "opencode_cwd": opencode_cwd,
        "spec": spec,
        "status": "running",
    }
    if run.get("base_commit"):
        state["base_commit"] = run["base_commit"]
    if project_spec_text:
        state["project_spec"] = project_spec_text
    if project_dir:
        state["project_dir"] = project_dir
    if run.get("replay_of"):
        state["replay_run_id"] = run["replay_of"]
    return state


def retry_run(run_id: int, *, db_path: Path, on_event: OnEvent | None = None) -> RunOutcome:
    """Re-drive a run that died after the operator answered a checkpoint.

    A transient provider error between `factory reject` and the re-run left the
    decision stranded: the run was no longer `waiting_human`, so approve/reject
    refused it, and the recorded answer had no reader. This puts the run back
    where it was and replays the SAME decision — no re-interruption.
    """
    emit = on_event or ignore_events
    init_db(db_path)
    with get_db(db_path) as conn:
        run = get_run(conn, run_id)
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
        requeue_answered_gate(conn, run_id, gate["id"])
        conn.commit()

    emit(RetryStarted(run_id, gate["gate_name"], action, feedback))
    return resume_run(run_id, action, reason=feedback or None, db_path=db_path, on_event=on_event)


def _refuse_if_project_busy(conn: Any, project_id: str, *, except_run: int | None = None) -> None:
    """One live run per product repo: the coder's checkpoint stages the whole working
    tree, so two stories at once would commit each other's changes (review T10)."""
    busy = [r for r in live_runs_in_project(conn, project_id) if r != except_run]
    if busy:
        raise RunError(
            f"Run #{busy[0]} is already working in project {project_id}; two stories in one "
            "repository would commit each other's changes. Wait for it to stop, or run "
            "`factory reconcile` if its process died."
        )


def _require_agents_link(repo: Path) -> None:
    """Refuse a live project run whose agents are not THIS checkout's.

    Each product's `.opencode` is a symlink into one factory checkout. Launched
    from another checkout (a clone, a worktree), the run would silently drive the
    first checkout's agents — not the ones `make evals` just validated here.
    """
    problem = agents_link_problem(repo)
    if problem:
        raise RunError(problem)


def _unresumable(run_id: int, reason: str, db_path: Path) -> None:
    """Park the run as blocked (never leave it 'running'), then refuse."""
    with get_db(db_path) as conn:
        park_unresumable(conn, run_id, reason)
    raise RunError(f"Cannot resume: {reason}")


def _finish(
    run_id: int, story_id: str, final_state: dict[str, Any], db_path: Path, emit: OnEvent,
    notify_operator: bool = True,
) -> RunOutcome:
    status = final_state.get("status", "unknown")
    # The questions of whichever checkpoint parked the run (newest stage first).
    human_qs = None
    if status == "waiting_human":
        human_qs = next(
            (qs for key in ("gate_release", "gate_2", "gate_1")
             if (qs := (final_state.get(key) or {}).get("human_questions"))),
            None,
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
    _commit_pipeline_record(run_id, story_id, final_state, db_path)
    emit(RunFinished(outcome))
    if notify_operator:
        _notify_if_parked(run_id, story_id, status, outcome.current_stage)
    return outcome


def _commit_pipeline_record(
    run_id: int, story_id: str, final_state: dict[str, Any], db_path: Path
) -> None:
    """Commit the boss's PIPELINE.md for this stop into the product repo.

    Every way a run stops — completed, failed, blocked, parked for the operator —
    passes through `_finish`, so this is the one place the record is written.
    Evidence must never fail a run; `git_commit_paths` is already best-effort.
    """
    project_dir = final_state.get("project_dir")
    if not project_dir:
        return
    try:
        path = write_pipeline_record(db_path, run_id, Path(project_dir))
    except (OSError, sqlite3.Error):
        return
    if path:
        git_commit_paths(Path(project_dir), [path], f"factory: {story_id} PIPELINE (run {run_id})")


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
