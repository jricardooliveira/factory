"""Node: tester-agent — QA / security / performance review of the cumulative change."""

from __future__ import annotations

from typing import Any

from factory.domain.contracts import TesterOutput
from factory.pipeline.agent_calls import db_conn, run_agent_json, usage_kwargs
from factory.pipeline.prompts.tester import build_tester_prompt
from factory.pipeline.state import PipelineState
from factory.state.db import finish_run, log_agent, update_run_stage


def node_tester_agent(state: PipelineState) -> dict[str, Any]:
    if state.get("status") in ("failed", "blocked"):
        return state

    conn = db_conn(state)
    try:
        update_run_stage(conn, state["run_id"], "tester-agent")
        conn.commit()

        prompt = build_tester_prompt(state)

        result, parsed = run_agent_json(state, "tester-agent", prompt)
        if parsed.get("error") == "Agent did not return valid JSON":
            agent_said = parsed.get("agent_response", "unknown")
            log_agent(conn, state["run_id"], "tester-agent", prompt, result.output,
                      verdict="blocked", duration_secs=result.duration_secs,
                      **usage_kwargs(result))
            finish_run(conn, state["run_id"], "blocked", error=f"tester off-script: {agent_said}")
            conn.commit()
            return {"tester_raw": result.output,
                    "tester": {"overall": "blocked", "summary": agent_said},
                    "status": "blocked", "error": f"tester-agent did not return JSON: {agent_said}"}

        tester = TesterOutput.model_validate(parsed)
        log_agent(conn, state["run_id"], "tester-agent", prompt, result.output,
                  verdict=tester.overall, duration_secs=result.duration_secs,
                  **usage_kwargs(result))
        conn.commit()
        return {"tester_raw": result.output, "tester": parsed}
    except Exception as e:
        log_agent(conn, state["run_id"], "tester-agent", "", str(e), verdict="error")
        finish_run(conn, state["run_id"], "failed", error=f"tester-agent failed: {e}")
        conn.commit()
        return {"status": "failed", "error": f"tester-agent failed: {e}"}
    finally:
        conn.close()
