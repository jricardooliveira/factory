"""Node: architect-agent — designs the technical approach and records it as an ADR."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factory.domain.contracts import ArchitectOutput, SpecOutput
from factory.evidence.adr import write_adr
from factory.pipeline.agent_calls import _get_db_conn, _run_agent_json, _usage_kwargs
from factory.pipeline.nodes.evidence import _commit_adr, _write_chain_artifact
from factory.pipeline.prompts.architect import build_architect_prompt
from factory.pipeline.state import PipelineState
from factory.state.db import finish_run, log_agent, update_run_stage, update_story_status


def node_architect_agent(state: PipelineState) -> dict[str, Any]:
    if state.get("status") == "failed":
        return state

    conn = _get_db_conn(state)
    try:
        update_run_stage(conn, state["run_id"], "architect-agent")
        conn.commit()

        prompt = build_architect_prompt(state)

        result, parsed = _run_agent_json(state, "architect-agent", prompt)

        # Handle synthetic blocked response
        if parsed.get("error") == "Agent did not return valid JSON":
            log_agent(
                conn, state["run_id"], "architect-agent", prompt,
                result.output, verdict="blocked", duration_secs=result.duration_secs,
                **_usage_kwargs(result),
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
            **_usage_kwargs(result),
        )
        conn.commit()

        # Persist the decision as an ADR (decision memory). Project runs only.
        adr_path = None
        if state.get("project_dir") and arch.verdict != "fail":
            spec = SpecOutput.model_validate(state["spec"])
            adr_path = str(
                write_adr(Path(state["project_dir"]), state["story_id"], spec.title, arch)
            )
            _commit_adr(state, adr_path)

        out: dict[str, Any] = {"architect_raw": result.output, "architect": parsed}
        if adr_path:
            out["adr_path"] = adr_path
        # Link 3: the plan. `order_tasks` already computes the dependency order on
        # every coder call and threw it away; committing it makes the sequence a
        # reviewer reads the sequence that ran, and its declared scope is what the
        # trust package checks the real diff against.
        if arch.verdict != "fail":
            plan_path = _write_chain_artifact(
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
