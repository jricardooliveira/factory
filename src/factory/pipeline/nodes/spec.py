"""Node: spec-agent — turns the operator's request into a story."""

from __future__ import annotations

from typing import Any

from factory.domain.contracts import SpecOutput
from factory.pipeline.agent_calls import db_conn, run_agent_json, usage_kwargs
from factory.pipeline.evidence_writers import write_chain_artifact
from factory.pipeline.prompts.spec import build_spec_prompt
from factory.pipeline.state import PipelineState
from factory.state.db import finish_run, log_agent, update_run_stage, update_story_title


def node_spec_agent(state: PipelineState) -> dict[str, Any]:
    conn = db_conn(state)
    try:
        update_run_stage(conn, state["run_id"], "spec-agent")
        conn.commit()

        # Link 1 of the chain: the operator's raw ask, on disk with an author and a
        # date, BEFORE any agent interprets it — so a run that dies at the spec
        # still leaves a record of what was asked.
        intent_path = write_chain_artifact(state, "intent", state.get("request", ""),
                                           project_id=state.get("project_id", ""))

        prompt = build_spec_prompt(state)
        result, parsed = run_agent_json(state, "spec-agent", prompt)

        # Handle synthetic blocked response from _extract_json
        if parsed.get("error") == "Agent did not return valid JSON":
            agent_said = parsed.get("agent_response", "unknown")
            error = f"spec-agent did not return JSON. Agent said: {agent_said}"
            log_agent(
                conn, state["run_id"], "spec-agent", prompt,
                result.output, verdict="blocked", duration_secs=result.duration_secs,
                **usage_kwargs(result),
            )
            finish_run(conn, state["run_id"], "blocked", error=error)
            conn.commit()
            return {
                "spec_raw": result.output,
                "spec": {
                    "verdict": "blocked", "title": "", "acceptance_criteria": [], "tasks": [],
                    "questions": [f"Agent went off-script: {agent_said}"],
                },
                "status": "blocked",
                "error": error,
            }

        spec = SpecOutput.model_validate(parsed)

        log_agent(
            conn, state["run_id"], "spec-agent", prompt,
            result.output, verdict=spec.verdict, duration_secs=result.duration_secs,
            **usage_kwargs(result),
        )
        # Give the story its real title. Do NOT overwrite the canonical story_id
        # (state["story_id"] is the DB row); the agent's spec.story_id is its own
        # numbering and clobbering it would orphan later story-row updates.
        if spec.title:
            update_story_title(conn, state["story_id"], spec.title)
        conn.commit()

        # Link 2: the story the design must answer. The ADR recorded the decision
        # but never the requirements it was a decision about.
        spec_path = write_chain_artifact(state, "spec", spec)

        out: dict[str, Any] = {"spec_raw": result.output, "spec": parsed}
        if intent_path:
            out["intent_path"] = intent_path
        if spec_path:
            out["spec_path"] = spec_path
        return out
    except Exception as e:
        log_agent(
            conn, state["run_id"], "spec-agent",
            locals().get("prompt") or state.get("request", ""), str(e), verdict="error",
        )
        finish_run(conn, state["run_id"], "failed", error=f"spec-agent failed: {e}")
        conn.commit()
        return {"status": "failed", "error": f"spec-agent failed: {e}"}
    finally:
        conn.close()
