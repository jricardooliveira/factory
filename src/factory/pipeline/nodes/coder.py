"""Node: coder-agent — one task per call, the build gate, and tester-driven remediation.

Code reaches disk only through `materialize_code_blocks`; any file that changes
without being declared in `code_blocks` is an out-of-band write and blocks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from factory.agent_config.settings import settings
from factory.domain.contracts import CoderOutput, SpecOutput, TaskDef
from factory.domain.gates import MAX_CODER_ATTEMPTS, MAX_REARCHITECT_LOOPS
from factory.domain.task_order import order_tasks
from factory.pipeline.agent_calls import (
    budget_refusal_for,
    db_conn,
    run_agent_json,
    spend_so_far,
    usage_kwargs,
)
from factory.pipeline.prompts.coder import build_coder_task_prompt, build_remediation_prompt
from factory.pipeline.state import PipelineState, factory_owned_paths
from factory.state.db import (
    finish_run,
    log_agent,
    log_gate,
    update_run_stage,
    update_story_status,
)
from factory.verification import VerifyResult, verify_changes
from factory.verification.scope import (
    declared_scope_mismatch,
    paths_outside_scope,
    scope_note,
)
from factory.workspace.git import (
    collect_repo_diff,
    git_changed_paths,
    git_commit_all,
    git_discard_paths,
)
from factory.workspace.layout import is_evidence_path
from factory.workspace.materialize import materialize_code_blocks, normalize_block_path


def _changed_files(
    written: list[Path], root: Path, *, exclude: tuple[str, ...] = ()
) -> set[str]:
    """What this coder pass actually changed, preferring the git-measured truth.

    `exclude` = the factory-owned evidence paths of a project repo: the factory
    wrote those, so they are never an agent's undeclared write. Without git, fall
    back to the files materialize reports it wrote.
    """
    git_changed = git_changed_paths(root, exclude=exclude)
    if git_changed is not None:
        return set(git_changed)
    return _relative(written, root)


def _relative(written: list[Path], root: Path) -> set[str]:
    root_resolved = root.resolve()
    actual: set[str] = set()
    for p in written:
        try:
            actual.add(str(Path(p).resolve().relative_to(root_resolved)))
        except ValueError:
            actual.add(str(p))
    return actual


def _scope_diff(
    coder: CoderOutput, written: list[Path], root: Path, *, exclude: tuple[str, ...] = (),
    earlier: set[str] = frozenset(),
) -> tuple[set[str], set[str]]:
    """(unclaimed, missing) for this pass — see `verification.scope`.

    `earlier` = what the factory materialized on this task's failed attempts: it is
    only committed once the task passes, so git still lists it as changed, but the
    factory wrote it — never this agent's out-of-band write.
    """
    claimed = {c.path for c in coder.code_blocks}
    unclaimed, missing = declared_scope_mismatch(
        _changed_files(written, root, exclude=exclude), claimed)
    return unclaimed - earlier, missing


def _materialize(state: PipelineState, coder: CoderOutput) -> tuple[Path, tuple[str, ...], list[Path]]:
    """Write the coder's code_blocks; return (root, factory-owned paths, written)."""
    root = Path(state.get("opencode_cwd") or ".")
    # Paths the factory owns in a project repo (its evidence): never the
    # coder's to write, never counted as the coder's change.
    owned = factory_owned_paths(state)
    # Agents prefix paths with 'repo/' but cwd IS the repo -> de-double so
    # files land at the repo root and claimed==actual for the scope check.
    for block in coder.code_blocks:
        block.path = normalize_block_path(root, block.path, repo_root=bool(owned))
    written = materialize_code_blocks(list(coder.code_blocks), root=root, reserved=owned)
    return root, owned, written


def _outside_scope(state: PipelineState, coder: CoderOutput, scope: list[str]) -> list[str]:
    """Declared code_blocks that fall outside `scope` — checked BEFORE anything lands.

    Operator decision (review task T06): out-of-scope writes are refused and retried,
    not merely reported at Checkpoint 3. Tests and toolchain manifests stay allowed
    and an empty scope forbids nothing (`verification.scope.paths_outside_scope`).
    """
    root = Path(state.get("opencode_cwd") or ".")
    owned = factory_owned_paths(state)
    paths = [normalize_block_path(root, b.path, repo_root=bool(owned)) for b in coder.code_blocks]
    # A factory-owned path (PROJECT_RULES.md, docs/work/...) is materialize's to refuse,
    # with its own precise reason — not a matter of task scope.
    paths = [p for p in paths if not (owned and is_evidence_path(p, owned))]
    return sorted(set(paths_outside_scope(paths, scope)))


def _scope_reason(label: str, outside: list[str], scope: list[str]) -> str:
    return (
        f"[{label}] SCOPE: writes outside the declared files were refused: {outside} "
        f"(allowed: {scope}). Write only inside that scope — tests and manifests "
        "(go.mod, package.json, ...) are always allowed."
    )


def _story_scope(spec: SpecOutput) -> list[str]:
    """A remediation pass may touch what ANY task declared; if one task declared
    nothing, the story is unrestricted (an empty scope forbids nothing)."""
    if not spec.tasks or any(not t.scope for t in spec.tasks):
        return []
    return [s for t in spec.tasks for s in t.scope]


def _discard_attempt(state: PipelineState, written: list[Path] | None = None) -> None:
    """A run that stops mid-coding undoes the factory's own uncommitted writes
    (this pass's `written` + earlier attempts'), so the next story on the repo
    doesn't inherit them as 'out-of-band'. Only those paths: the out-of-band
    file itself, operator files and evidence stay (see `git_discard_paths`)."""
    if not state.get("opencode_cwd"):
        return  # never guess a repo: "." would be wherever the factory runs
    root = Path(state["opencode_cwd"])
    git_discard_paths(root, set(state.get("attempt_written") or []) | _relative(written or [], root))


def _with_failures(headline: str, verify_result: VerifyResult) -> str:
    """The one-line verdict, then each failed check's error on its own line: the
    retry's prior_findings and the operator both need the WHY, and one-line
    displays (board cell, queue) keep only the first line."""
    return "\n".join([headline, *verify_result.failures])


def _block_out_of_band(
    conn: sqlite3.Connection, state: PipelineState, gate_reason: str,
    written: list[Path] | None = None,
) -> None:
    """Record an out-of-band write: gate-build fails, the run is BLOCKED (a retry
    won't help, and shipping un-vetted code defeats the gates)."""
    _discard_attempt(state, written)
    log_gate(conn, state["run_id"], "gate-build", False, gate_reason)
    update_story_status(conn, state["story_id"], "blocked")
    finish_run(conn, state["run_id"], "blocked", error=gate_reason)
    conn.commit()


def _fail_story(
    conn: sqlite3.Connection, state: PipelineState, error: str,
    written: list[Path] | None = None,
) -> None:
    _discard_attempt(state, written)
    update_story_status(conn, state["story_id"], "failed")
    finish_run(conn, state["run_id"], "failed", error=error)
    conn.commit()


def _coder_remediation(state: PipelineState, conn: sqlite3.Connection) -> dict[str, Any]:
    """One cross-cutting coder pass to resolve tester findings, then back to the
    tester. Runs on the frontier tier (escalated via attempt_number) because a
    remediation is reasoning-heavy. A pass that breaks the build stops the run.
    """
    spec = SpecOutput.model_validate(state["spec"])
    root = Path(state.get("opencode_cwd") or ".")
    owned = factory_owned_paths(state)
    diff = collect_repo_diff(root, exclude=owned, base=state.get("base_commit")) or ""
    prompt = build_remediation_prompt(state, spec, diff)

    result, parsed = run_agent_json(state, "coder-agent", prompt, slot="remediation")
    if parsed.get("error") == "Agent did not return valid JSON":
        agent_said = parsed.get("agent_response", "unknown")
        log_agent(conn, state["run_id"], "coder-agent", prompt, result.output,
                  verdict="blocked", duration_secs=result.duration_secs,
                  stage_type="remediation", **usage_kwargs(result))
        finish_run(conn, state["run_id"], "blocked",
                   error=f"[remediation] off-script: {agent_said}")
        conn.commit()
        return {"coder_raw": result.output, "coder": parsed, "remediation": False,
                "next_action": "give_up", "status": "blocked",
                "error": f"remediation coder did not return JSON: {agent_said}"}

    coder = CoderOutput.model_validate(parsed)
    outside = _outside_scope(state, coder, _story_scope(spec))
    if outside:
        log_agent(conn, state["run_id"], "coder-agent", prompt, result.output,
                  verdict=coder.verdict, duration_secs=result.duration_secs,
                  stage_type="remediation", **usage_kwargs(result))
        gate_reason = _scope_reason("remediation", outside, _story_scope(spec))
        log_gate(conn, state["run_id"], "gate-build", False, gate_reason)
        error = f"remediation failed gate-build: {gate_reason}"
        _fail_story(conn, state, error)
        return {"coder_raw": result.output, "coder": parsed, "remediation": False,
                "gate_build": {"passed": False, "verdict": "fail", "reason": gate_reason,
                               "task": "remediation"},
                "next_action": "give_up", "status": "failed", "error": error}
    root, owned, written = _materialize(state, coder)
    log_agent(conn, state["run_id"], "coder-agent", prompt, result.output,
              verdict=coder.verdict, duration_secs=result.duration_secs,
              stage_type="remediation", **usage_kwargs(result))

    verify_result = verify_changes(written, root=root)
    unclaimed, _missing = _scope_diff(coder, written, root, exclude=owned)
    if unclaimed:
        gate_reason = (
            f"[remediation] GOVERNANCE: out-of-band file writes not declared in "
            f"code_blocks: {sorted(unclaimed)}."
        )
        _block_out_of_band(conn, state, gate_reason, written)
        return {"coder_raw": result.output, "coder": parsed, "remediation": False,
                "gate_build": {"passed": False, "verdict": "fail", "reason": gate_reason,
                               "task": "remediation"},
                "next_action": "give_up", "status": "blocked", "error": gate_reason}

    gate_reason = _with_failures(f"[remediation] {verify_result.summary}", verify_result)
    log_gate(conn, state["run_id"], "gate-build", verify_result.passed, gate_reason)
    gate_build = {"passed": verify_result.passed, "verdict": verify_result.verdict,
                  "reason": gate_reason, "task": "remediation"}

    if verify_result.passed and coder.verdict == "complete":
        git_commit_all(root, "factory: remediation (tester findings)")
        conn.commit()
        # Back to the tester to re-judge the fixed implementation.
        return {"coder_raw": result.output, "coder": parsed, "gate_build": gate_build,
                "remediation": False, "next_action": "complete"}

    error = f"remediation failed gate-build: {gate_reason}"
    _fail_story(conn, state, error, written)
    return {"coder_raw": result.output, "coder": parsed, "gate_build": gate_build,
            "remediation": False, "next_action": "give_up", "status": "failed", "error": error}


def _off_script(
    conn: sqlite3.Connection, state: PipelineState, task: TaskDef, prompt: str,
    result: Any, parsed: dict[str, Any],
) -> dict[str, Any]:
    """The coder answered without JSON: the run is BLOCKED, never guessed at."""
    agent_said = parsed.get("agent_response", "unknown")
    _discard_attempt(state)
    log_agent(
        conn, state["run_id"], "coder-agent", prompt,
        result.output, verdict="blocked", duration_secs=result.duration_secs,
        stage_type=task.id, **usage_kwargs(result),
    )
    finish_run(conn, state["run_id"], "blocked",
               error=f"[{task.id}] Agent went off-script: {agent_said}")
    conn.commit()
    return {
        "coder_raw": result.output,
        "coder": {"verdict": "blocked",
                  "implementation_summary": f"[{task.id}] off-script: {agent_said}",
                  "assumptions": [agent_said]},
        "status": "blocked",
        "error": f"coder-agent did not return JSON for {task.id}. Agent said: {agent_said}",
    }


def _design_feedback(
    conn: sqlite3.Connection, state: PipelineState, task: TaskDef, prompt: str,
    result: Any, parsed: dict[str, Any], coder: CoderOutput,
) -> dict[str, Any]:
    """Coder → architect feedback: the design itself is infeasible.

    Honored only at the first task's first attempt (nothing committed yet → clean
    restart); later, or once the budget is spent, give up rather than ship code
    that works around a design known to be wrong.
    """
    task_index = state.get("task_index", 0)
    attempt = state.get("attempt_number", 1)
    count = state.get("rearchitect_count", 0)
    log_agent(conn, state["run_id"], "coder-agent", prompt, result.output,
              verdict="design-infeasible", duration_secs=result.duration_secs,
              stage_type=task.id, **usage_kwargs(result))
    if task_index == 0 and attempt == 1 and count < MAX_REARCHITECT_LOOPS:
        conn.commit()
        return {
            "coder_raw": result.output, "coder": parsed,
            "next_action": "rearchitect",
            "triggered_by": "design-infeasible",
            "prior_findings": [coder.design_feedback.strip()],
            "rearchitect_count": count + 1,
            "task_index": 0, "attempt_number": 1, "tasks_completed": [],
        }
    error = (
        f"coder reports the design is infeasible but re-architecture budget is "
        f"spent (max {MAX_REARCHITECT_LOOPS}): {coder.design_feedback.strip()}"
    )
    _fail_story(conn, state, error)
    return {"coder_raw": result.output, "coder": parsed,
            "next_action": "give_up", "status": "failed", "error": error}


def _route_after_build(
    conn: sqlite3.Connection, state: PipelineState, tasks: list[TaskDef], task: TaskDef,
    coder: CoderOutput, base: dict[str, Any], verify_passed: bool, gate_reason: str,
    root: Path, written: list[Path] | None = None,
) -> dict[str, Any]:
    """After a clean gate-build log: advance, retry the same task, or give up."""
    task_index = state.get("task_index", 0)
    attempt = state.get("attempt_number", 1)
    completed = list(state.get("tasks_completed", []))

    # Task succeeded? Advance to the next task, or complete the story.
    if verify_passed and coder.verdict == "complete":
        completed = completed + [task.id]
        # Checkpoint this task so the NEXT task's scope check sees only its
        # own new files (otherwise prior tasks' files look "out-of-band").
        git_commit_all(root, f"factory: {task.id} {task.title}")
        conn.commit()
        if task_index + 1 >= len(tasks):
            # Coding done — hand to the tester gate, which finalizes the run.
            return {**base, "next_action": "complete", "tasks_completed": completed,
                    "attempt_written": []}
        return {**base, "next_action": "next_task", "task_index": task_index + 1,
                "attempt_number": 1, "prior_findings": [], "tasks_completed": completed,
                "attempt_written": []}

    # Task failed: retry the SAME task within budget, else give up + queue.
    conn.commit()  # the spend below is read on its own connection
    budget_left = attempt < MAX_CODER_ATTEMPTS and budget_refusal_for(state) is None
    if not verify_passed and budget_left:
        conn.commit()  # keep run 'running'; same task_index -> retries this task
        return {
            **base,
            "next_action": "retry",
            "attempt_number": attempt + 1,
            "triggered_by": "gate-build",
            "prior_findings": [gate_reason],
            # Uncommitted until the task passes: the retry's scope check must
            # not read the factory's own earlier writes as the agent's.
            "attempt_written": sorted(
                set(state.get("attempt_written") or []) | _relative(written or [], root)),
        }

    if not verify_passed:
        error = (
            f"gate-build failed on {task.id} after {attempt} attempt(s) "
            f"(budget: {MAX_CODER_ATTEMPTS} attempts; story spend "
            f"~${spend_so_far(state).estimated_usd:.2f} of ${settings().budget.max_story_cost_usd:.2f}): "
            f"{gate_reason}"
        )
    else:
        error = f"coder reported verdict '{coder.verdict}' on {task.id}"
    _fail_story(conn, state, error, written)
    return {**base, "next_action": "give_up", "status": "failed", "error": error}


def _implement_task(
    conn: sqlite3.Connection, state: PipelineState, spec: SpecOutput, tasks: list[TaskDef]
) -> dict[str, Any]:
    """One coder call for the current task, then gate-build and routing."""
    task_index = state.get("task_index", 0)
    task = tasks[task_index]
    prompt = build_coder_task_prompt(
        state,
        task,
        spec,
        task_index=task_index,
        task_count=len(tasks),
        completed=list(state.get("tasks_completed", [])),
        attempt=state.get("attempt_number", 1),
    )

    result, parsed = run_agent_json(state, "coder-agent", prompt, slot=task.id)
    if parsed.get("error") == "Agent did not return valid JSON":
        return _off_script(conn, state, task, prompt, result, parsed)

    coder = CoderOutput.model_validate(parsed)
    if coder.design_feedback.strip():
        return _design_feedback(conn, state, task, prompt, result, parsed, coder)

    outside = _outside_scope(state, coder, task.scope)
    if outside:
        # Refused before anything lands: the retry starts from a clean tree.
        log_agent(conn, state["run_id"], "coder-agent", prompt, result.output,
                  verdict=coder.verdict, duration_secs=result.duration_secs,
                  stage_type=task.id, **usage_kwargs(result))
        gate_reason = _scope_reason(task.id, outside, task.scope)
        log_gate(conn, state["run_id"], "gate-build", False, gate_reason)
        base = {"coder_raw": result.output, "coder": parsed,
                "gate_build": {"passed": False, "verdict": "fail", "reason": gate_reason,
                               "task": task.id}}
        return _route_after_build(conn, state, tasks, task, coder, base, False, gate_reason,
                                  Path(state.get("opencode_cwd") or "."))

    root, owned, written = _materialize(state, coder)
    log_agent(
        conn, state["run_id"], "coder-agent", prompt, result.output,
        verdict=coder.verdict, duration_secs=result.duration_secs,
        stage_type=task.id, **usage_kwargs(result),
    )

    # ── gate-build: verify the cumulative repo after this task ──
    verify_result = verify_changes(written, root=root)
    unclaimed, missing = _scope_diff(coder, written, root, exclude=owned,
                                     earlier=set(state.get("attempt_written") or []))
    note = scope_note(unclaimed, missing)
    gate_reason = f"[{task.id}] {verify_result.summary}"
    if note:
        gate_reason += f"; {note}"
    gate_reason = _with_failures(gate_reason, verify_result)

    # Governance: any file that landed in the repo but was NOT declared in
    # code_blocks is an out-of-band write (agent bypassing materialize).
    if unclaimed:
        gate_reason = (
            f"[{task.id}] GOVERNANCE: out-of-band file writes not declared in "
            f"code_blocks: {sorted(unclaimed)}. Agents must return code via "
            f"code_blocks, not write files directly."
        )
        _block_out_of_band(conn, state, gate_reason, written)
        return {
            "coder_raw": result.output, "coder": parsed,
            "gate_build": {"passed": False, "verdict": "fail", "reason": gate_reason,
                           "task": task.id},
            "next_action": "give_up", "status": "blocked", "error": gate_reason,
        }

    log_gate(conn, state["run_id"], "gate-build", verify_result.passed, gate_reason)
    gate_build = {
        "passed": verify_result.passed,
        "verdict": verify_result.verdict,
        "reason": gate_reason,
        "task": task.id,
    }
    base = {"coder_raw": result.output, "coder": parsed, "gate_build": gate_build}
    return _route_after_build(
        conn, state, tasks, task, coder, base, verify_result.passed, gate_reason, root, written
    )


def node_coder_agent(state: PipelineState) -> dict[str, Any]:
    """Implement ONE task per invocation (dependency-ordered), then route back
    for the next task or a bounded per-task retry. This makes the spec-agent's
    decomposition executional and keeps each LLM call small enough to finish.
    """
    if state.get("status") == "failed":
        return state

    conn = db_conn(state)
    try:
        update_run_stage(conn, state["run_id"], "coder-agent")
        conn.commit()

        # Tester-driven remediation: a cross-cutting fix pass, not a per-task call.
        if state.get("remediation"):
            return _coder_remediation(state, conn)

        spec = SpecOutput.model_validate(state["spec"])
        tasks = order_tasks(list(spec.tasks))
        if not tasks:
            finish_run(conn, state["run_id"], "failed", error="No tasks to implement")
            conn.commit()
            return {"status": "failed", "error": "No tasks to implement"}

        return _implement_task(conn, state, spec, tasks)
    except Exception as e:
        _discard_attempt(state)
        log_agent(conn, state["run_id"], "coder-agent", "", str(e), verdict="error")
        finish_run(conn, state["run_id"], "failed", error=str(e))
        conn.commit()
        return {"status": "failed", "error": f"coder-agent failed: {e}"}
    finally:
        conn.close()
