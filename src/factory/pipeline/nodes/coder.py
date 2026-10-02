"""Node: coder-agent — one task per call, the build gate, and tester-driven remediation.

Code reaches disk only through `materialize_code_blocks`; any file that changes
without being declared in `code_blocks` is an out-of-band write and blocks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from factory.domain.contracts import CoderOutput, SpecOutput
from factory.domain.gates import MAX_CODER_ATTEMPTS, MAX_REARCHITECT_LOOPS, MAX_TASK_COST_USD
from factory.domain.task_order import order_tasks
from factory.pipeline.agent_calls import _get_db_conn, _run_agent_json, _usage_kwargs
from factory.pipeline.prompts.coder import build_coder_task_prompt, build_remediation_prompt
from factory.pipeline.state import PipelineState, factory_owned_paths
from factory.state.db import (
    finish_run,
    get_run_cost,
    log_agent,
    log_gate,
    update_run_stage,
    update_story_status,
)
from factory.verification import verify_changes
from factory.workspace.git import collect_repo_diff, git_changed_paths, git_commit_all
from factory.workspace.materialize import materialize_code_blocks, normalize_block_path


def _scope_diff(
    coder: CoderOutput, written: list[Path], root: Path, *, exclude: tuple[str, ...] = ()
) -> tuple[set[str], set[str]]:
    """Return (unclaimed, missing) file sets, preferring the git-measured truth.

    `unclaimed` = files that changed in the repo but the coder did NOT declare in
    code_blocks — i.e. out-of-band writes (an agent scribbling outside the
    factory's controlled materialize path). `missing` = declared but not on disk.
    `exclude` = the factory-owned evidence paths of a project repo: the factory
    wrote those, so they are never an agent's undeclared write.
    """
    git_changed = git_changed_paths(root, exclude=exclude)
    if git_changed is None:
        root_resolved = root.resolve()
        actual: set[str] = set()
        for p in written:
            try:
                actual.add(str(Path(p).resolve().relative_to(root_resolved)))
            except ValueError:
                actual.add(str(p))
    else:
        actual = set(git_changed)
    claimed = {c.path for c in coder.code_blocks}
    return actual - claimed, claimed - actual


def _scope_note(unclaimed: set[str], missing: set[str]) -> str | None:
    if not unclaimed and not missing:
        return None
    bits = []
    if unclaimed:
        bits.append(f"unclaimed: {sorted(unclaimed)}")
    if missing:
        bits.append(f"claimed-but-absent: {sorted(missing)}")
    return "scope-mismatch (" + "; ".join(bits) + ")"


def _coder_remediation(state: PipelineState, conn: sqlite3.Connection) -> dict[str, Any]:
    """One cross-cutting coder pass to resolve tester findings, then back to the
    tester. Runs on the frontier tier (escalated via attempt_number) because a
    remediation is reasoning-heavy. A pass that breaks the build stops the run.
    """
    spec = SpecOutput.model_validate(state["spec"])
    root = Path(state.get("opencode_cwd") or ".")
    owned = factory_owned_paths(state)
    diff = collect_repo_diff(root, exclude=owned) or ""
    prompt = build_remediation_prompt(state, spec, diff)

    result, parsed = _run_agent_json(state, "coder-agent", prompt, slot="remediation")
    if parsed.get("error") == "Agent did not return valid JSON":
        agent_said = parsed.get("agent_response", "unknown")
        log_agent(conn, state["run_id"], "coder-agent", prompt, result.output,
                  verdict="blocked", duration_secs=result.duration_secs,
                  stage_type="remediation", **_usage_kwargs(result))
        finish_run(conn, state["run_id"], "blocked",
                   error=f"[remediation] off-script: {agent_said}")
        conn.commit()
        return {"coder_raw": result.output, "coder": parsed, "remediation": False,
                "next_action": "give_up", "status": "blocked",
                "error": f"remediation coder did not return JSON: {agent_said}"}

    coder = CoderOutput.model_validate(parsed)
    for block in coder.code_blocks:
        block.path = normalize_block_path(root, block.path, repo_root=bool(owned))
    written = materialize_code_blocks(list(coder.code_blocks), root=root, reserved=owned)
    log_agent(conn, state["run_id"], "coder-agent", prompt, result.output,
              verdict=coder.verdict, duration_secs=result.duration_secs,
              stage_type="remediation", **_usage_kwargs(result))

    verify_result = verify_changes(written, root=root)
    unclaimed, _missing = _scope_diff(coder, written, root, exclude=owned)
    if unclaimed:
        gate_reason = (
            f"[remediation] GOVERNANCE: out-of-band file writes not declared in "
            f"code_blocks: {sorted(unclaimed)}."
        )
        log_gate(conn, state["run_id"], "gate-build", False, gate_reason)
        update_story_status(conn, state["story_id"], "blocked")
        finish_run(conn, state["run_id"], "blocked", error=gate_reason)
        conn.commit()
        return {"coder_raw": result.output, "coder": parsed, "remediation": False,
                "gate_build": {"passed": False, "verdict": "fail", "reason": gate_reason,
                               "task": "remediation"},
                "next_action": "give_up", "status": "blocked", "error": gate_reason}

    gate_reason = f"[remediation] {verify_result.summary}"
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
    update_story_status(conn, state["story_id"], "failed")
    finish_run(conn, state["run_id"], "failed", error=error)
    conn.commit()
    return {"coder_raw": result.output, "coder": parsed, "gate_build": gate_build,
            "remediation": False, "next_action": "give_up", "status": "failed", "error": error}


def node_coder_agent(state: PipelineState) -> dict[str, Any]:
    """Implement ONE task per invocation (dependency-ordered), then route back
    for the next task or a bounded per-task retry. This makes the spec-agent's
    decomposition executional and keeps each LLM call small enough to finish.
    """
    if state.get("status") == "failed":
        return state

    conn = _get_db_conn(state)
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

        task_index = state.get("task_index", 0)
        completed = list(state.get("tasks_completed", []))
        attempt = state.get("attempt_number", 1)
        task = tasks[task_index]

        prompt = build_coder_task_prompt(
            state,
            task,
            spec,
            task_index=task_index,
            task_count=len(tasks),
            completed=completed,
            attempt=attempt,
        )

        result, parsed = _run_agent_json(state, "coder-agent", prompt, slot=task.id)

        # Handle synthetic blocked response
        if parsed.get("error") == "Agent did not return valid JSON":
            agent_said = parsed.get("agent_response", "unknown")
            log_agent(
                conn, state["run_id"], "coder-agent", prompt,
                result.output, verdict="blocked", duration_secs=result.duration_secs,
                stage_type=task.id, **_usage_kwargs(result),
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

        coder = CoderOutput.model_validate(parsed)

        # Coder → architect feedback: the design itself is infeasible. Honored only
        # at the first task's first attempt (nothing committed yet → clean restart);
        # later, or once the budget is spent, park for the human rather than ship
        # code that works around a design known to be wrong.
        if coder.design_feedback.strip():
            count = state.get("rearchitect_count", 0)
            log_agent(conn, state["run_id"], "coder-agent", prompt, result.output,
                      verdict="design-infeasible", duration_secs=result.duration_secs,
                      stage_type=task.id, **_usage_kwargs(result))
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
            update_story_status(conn, state["story_id"], "failed")
            finish_run(conn, state["run_id"], "failed", error=error)
            conn.commit()
            return {"coder_raw": result.output, "coder": parsed,
                    "next_action": "give_up", "status": "failed", "error": error}

        root = Path(state.get("opencode_cwd") or ".")
        # Paths the factory owns in a project repo (its evidence): never the
        # coder's to write, never counted as the coder's change.
        owned = factory_owned_paths(state)
        # Agents prefix paths with 'repo/' but cwd IS the repo -> de-double so
        # files land at the repo root and claimed==actual for the scope check.
        for block in coder.code_blocks:
            block.path = normalize_block_path(root, block.path, repo_root=bool(owned))
        written = materialize_code_blocks(list(coder.code_blocks), root=root, reserved=owned)

        log_agent(
            conn, state["run_id"], "coder-agent", prompt, result.output,
            verdict=coder.verdict, duration_secs=result.duration_secs,
            stage_type=task.id, **_usage_kwargs(result),
        )

        # ── gate-build: verify the cumulative repo after this task ──
        verify_result = verify_changes(written, root=root)
        unclaimed, missing = _scope_diff(coder, written, root, exclude=owned)
        scope_note = _scope_note(unclaimed, missing)
        gate_reason = f"[{task.id}] {verify_result.summary}"
        if scope_note:
            gate_reason += f"; {scope_note}"

        # Governance: any file that landed in the repo but was NOT declared in
        # code_blocks is an out-of-band write (agent bypassing materialize). Block
        # it — retrying won't help, and shipping un-vetted code defeats the gates.
        if unclaimed:
            gate_reason = (
                f"[{task.id}] GOVERNANCE: out-of-band file writes not declared in "
                f"code_blocks: {sorted(unclaimed)}. Agents must return code via "
                f"code_blocks, not write files directly."
            )
            log_gate(conn, state["run_id"], "gate-build", False, gate_reason)
            update_story_status(conn, state["story_id"], "blocked")
            finish_run(conn, state["run_id"], "blocked", error=gate_reason)
            conn.commit()
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

        # Task succeeded? Advance to the next task, or complete the story.
        if verify_result.passed and coder.verdict == "complete":
            completed = completed + [task.id]
            # Checkpoint this task so the NEXT task's scope check sees only its
            # own new files (otherwise prior tasks' files look "out-of-band").
            git_commit_all(root, f"factory: {task.id} {task.title}")
            if task_index + 1 >= len(tasks):
                # Coding done — hand to the tester gate, which finalizes the run.
                conn.commit()
                return {"coder_raw": result.output, "coder": parsed, "gate_build": gate_build,
                        "next_action": "complete", "tasks_completed": completed}
            conn.commit()
            return {"coder_raw": result.output, "coder": parsed, "gate_build": gate_build,
                    "next_action": "next_task", "task_index": task_index + 1,
                    "attempt_number": 1, "prior_findings": [], "tasks_completed": completed}

        # Task failed: retry the SAME task within budget, else give up + queue.
        cost_so_far = get_run_cost(conn, state["run_id"])
        budget_left = attempt < MAX_CODER_ATTEMPTS and cost_so_far < MAX_TASK_COST_USD
        if not verify_result.passed and budget_left:
            conn.commit()  # keep run 'running'; same task_index -> retries this task
            return {
                "coder_raw": result.output,
                "coder": parsed,
                "gate_build": gate_build,
                "next_action": "retry",
                "attempt_number": attempt + 1,
                "triggered_by": "gate-build",
                "prior_findings": [gate_reason],
            }

        if not verify_result.passed:
            error = (
                f"gate-build failed on {task.id} after {attempt} attempt(s) "
                f"(budget: {MAX_CODER_ATTEMPTS} attempts / ${MAX_TASK_COST_USD:.2f}, "
                f"spent ${cost_so_far:.4f}): {gate_reason}"
            )
        else:
            error = f"coder reported verdict '{coder.verdict}' on {task.id}"
        update_story_status(conn, state["story_id"], "failed")
        finish_run(conn, state["run_id"], "failed", error=error)
        conn.commit()
        return {
            "coder_raw": result.output,
            "coder": parsed,
            "gate_build": gate_build,
            "next_action": "give_up",
            "status": "failed",
            "error": error,
        }
    except Exception as e:
        log_agent(conn, state["run_id"], "coder-agent", "", str(e), verdict="error")
        finish_run(conn, state["run_id"], "failed", error=str(e))
        conn.commit()
        return {"status": "failed", "error": f"coder-agent failed: {e}"}
    finally:
        conn.close()
