"""Node: boundary-agent — the pre-implementation boundary review (original brief §5.5).

Runs only when the design declares a boundary impact (`gates.boundary_review_reasons`;
the routing lives in `graph.route_after_architect`). The verdict that counts is
computed in code (`gates.boundary_overall`): a failed review sends the design back
to the architect with the required changes, at most `MAX_BOUNDARY_REDESIGNS` times;
after that gate-2 rejects it. Warnings and an unavailable review are gate-2's to
put in front of the operator at Checkpoint 2.
"""

from __future__ import annotations

from typing import Any

from factory.domain.contracts import BoundaryOutput
from factory.agent_config.settings import settings
from factory.domain.gates import boundary_findings, boundary_overall
from factory.pipeline.agent_calls import ReplayGap, db_conn, run_agent_json, usage_kwargs
from factory.pipeline.prompts.boundary import build_boundary_prompt
from factory.pipeline.state import PipelineState
from factory.state.db import log_agent, update_run_stage


def node_boundary_agent(state: PipelineState) -> dict[str, Any]:
    if state.get("status") in ("failed", "blocked"):
        return state

    conn = db_conn(state)
    prompt = ""
    try:
        update_run_stage(conn, state["run_id"], "boundary-agent")
        conn.commit()
        prompt = build_boundary_prompt(state)
        try:
            result, parsed = run_agent_json(state, "boundary-agent", prompt)
        except ReplayGap as gap:
            # A run recorded before the boundary-agent existed: replay it as it ran.
            log_agent(conn, state["run_id"], "boundary-agent", prompt,
                      f"skipped — {gap}", verdict="skipped")
            conn.commit()
            return {"boundary": {}, "boundary_status": "skipped", "boundary_redesign": False}

        if parsed.get("error") == "Agent did not return valid JSON":
            log_agent(conn, state["run_id"], "boundary-agent", prompt, result.output,
                      verdict="blocked", duration_secs=result.duration_secs,
                      **usage_kwargs(result))
            conn.commit()
            return {"boundary_raw": result.output, "boundary": {},
                    "boundary_status": "unavailable", "boundary_redesign": False}

        review = BoundaryOutput.model_validate(parsed)
        verdict = boundary_overall(review)
        log_agent(conn, state["run_id"], "boundary-agent", prompt, result.output,
                  verdict=verdict, duration_secs=result.duration_secs, **usage_kwargs(result))
        conn.commit()
        out: dict[str, Any] = {"boundary_raw": result.output, "boundary": parsed,
                               "boundary_status": "reviewed", "boundary_redesign": False}
        redesigns = state.get("boundary_redesigns", 0)
        if verdict == "fail" and redesigns < settings().budget.max_boundary_redesigns:
            out.update({
                "boundary_redesign": True,
                "boundary_redesigns": redesigns + 1,
                "triggered_by": "boundary-failed",
                "prior_findings": boundary_findings(review, "fail"),
            })
        return out
    except Exception as e:  # a crashed review is an unavailable one: the operator decides
        log_agent(conn, state["run_id"], "boundary-agent", prompt, str(e), verdict="error")
        conn.commit()
        return {"boundary": {}, "boundary_status": "unavailable", "boundary_redesign": False}
    finally:
        conn.close()
