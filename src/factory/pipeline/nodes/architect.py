"""Node: architect-agent — designs the technical approach and records it as an ADR."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factory.domain.contracts import ArchitectOutput, SpecOutput
from factory.domain.gates import MAX_MODULES_PER_STORY
from factory.evidence.adr import write_adr
from factory.pipeline.agent_calls import ReplayGap, db_conn, run_agent_json, usage_kwargs
from factory.pipeline.evidence_writers import commit_adr, write_chain_artifact
from factory.pipeline.prompts.architect import build_architect_prompt
from factory.pipeline.state import PipelineState
from factory.state.db import finish_run, log_agent, update_run_stage, update_story_status


def node_architect_agent(state: PipelineState) -> dict[str, Any]:
    if state.get("status") == "failed":
        return state

    conn = db_conn(state)
    try:
        update_run_stage(conn, state["run_id"], "architect-agent")
        conn.commit()

        prompt = build_architect_prompt(state)

        result, parsed = run_agent_json(state, "architect-agent", prompt)

        # Handle synthetic blocked response
        if parsed.get("error") == "Agent did not return valid JSON":
            log_agent(
                conn, state["run_id"], "architect-agent", prompt,
                result.output, verdict="blocked", duration_secs=result.duration_secs,
                **usage_kwargs(result),
            )
            agent_said = parsed.get("agent_response", "unknown")
            error = f"architect-agent did not return JSON. Agent said: {agent_said}"
            # PERSIST it. Returning 'failed' in graph state only left the run as
            # 'running' with a NULL error — invisible to `factory queue` and later
            # mislabelled by `reconcile` as a dead process.
            update_story_status(conn, state["story_id"], "failed")
            finish_run(conn, state["run_id"], "failed", error=error)
            conn.commit()
            return {
                "architect_raw": result.output,
                "architect": {
                    "verdict": "fail",
                    "architecture_notes": f"Agent went off-script: {agent_said}",
                    "modules_affected": [], "risks": [],
                },
                "status": "failed",
                "error": error,
            }

        arch = ArchitectOutput.model_validate(parsed)

        log_agent(
            conn,
            state["run_id"],
            "architect-agent",
            prompt,
            result.output,
            verdict=arch.verdict,
            duration_secs=result.duration_secs,
            **usage_kwargs(result),
        )
        conn.commit()

        # Over the file limit gate-2 would simply fail the run. The architect gets ONE
        # chance to fit the design first; whatever comes back, gate-2 still decides.
        if len(arch.modules_affected) > MAX_MODULES_PER_STORY:
            resize_prompt = prompt + (
                f"\n\n## Your design is too large\n\nIt touches {len(arch.modules_affected)} "
                f"files; a story's design may touch at most {MAX_MODULES_PER_STORY} files "
                "(code, templates, tests and docs counted together). Return the SAME design "
                "as JSON, made to fit: fewer layers and modules (do not split one small "
                "concern across several files), tests grouped into fewer files. Keep every "
                "acceptance criterion covered; if it truly cannot fit, set `verdict` to "
                "`fail` and say in `architecture_notes` how the story should be split."
            )
            try:
                again, reparsed = run_agent_json(state, "architect-agent", resize_prompt,
                                                 slot="resize")
            except ReplayGap:
                reparsed = {}  # a run recorded before this rule: replay what it had
            if reparsed and reparsed.get("error") != "Agent did not return valid JSON":
                resized = ArchitectOutput.model_validate(reparsed)
                log_agent(
                    conn, state["run_id"], "architect-agent", resize_prompt, again.output,
                    verdict=resized.verdict, duration_secs=again.duration_secs,
                    stage_type="resize", **usage_kwargs(again),
                )
                conn.commit()
                result, parsed, arch = again, reparsed, resized

        # Persist the decision as an ADR (decision memory). Project runs only.
        adr_path = None
        if state.get("project_dir") and arch.verdict != "fail":
            spec = SpecOutput.model_validate(state["spec"])
            adr_path = str(
                write_adr(Path(state["project_dir"]), state["story_id"], spec.title, arch)
            )
            commit_adr(state, adr_path)

        # A new design voids the previous design's boundary review: the route after
        # the architect decides afresh whether THIS design needs one.
        out: dict[str, Any] = {"architect_raw": result.output, "architect": parsed,
                               "boundary": {}, "boundary_status": ""}
        if adr_path:
            out["adr_path"] = adr_path
        # Link 3: the plan. `order_tasks` already computes the dependency order on
        # every coder call and threw it away; committing it makes the sequence a
        # reviewer reads the sequence that ran, and its declared scope is what the
        # trust package checks the real diff against.
        if arch.verdict != "fail":
            plan_path = write_chain_artifact(
                state, "plan", SpecOutput.model_validate(state["spec"]), arch
            )
            if plan_path:
                out["plan_path"] = plan_path
        return out
    except Exception as e:
        error = f"architect-agent failed: {e}"
        log_agent(conn, state["run_id"], "architect-agent", "", str(e), verdict="error")
        update_story_status(conn, state["story_id"], "failed")
        finish_run(conn, state["run_id"], "failed", error=error)
        conn.commit()
        return {"status": "failed", "error": error}
    finally:
        conn.close()
