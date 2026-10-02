"""Release: the release-agent's notes, and the operator's release (Checkpoint 3).

`node_release_agent` writes RELEASE.md — words only; release notes are evidence,
so their failure is a named gap at gate-release, never a crash of a run whose
code already passed review. `node_release` is not an agent: it is what an
operator's approval at Checkpoint 3 does, and the boss runs it only when that
approval is on record.
"""

from __future__ import annotations

from typing import Any

from factory.domain.contracts import ReleaseOutput, SpecOutput
from factory.pipeline.agent_calls import ReplayGap, db_conn, run_agent_json, usage_kwargs
from factory.pipeline.delivery import merge_release
from factory.pipeline.evidence_writers import (
    release_evidence_gaps,
    write_chain_artifact,
    write_trust_package,
)
from factory.pipeline.prompts.release import build_release_prompt
from factory.pipeline.state import PipelineState
from factory.state.db import finish_run, log_agent, update_run_stage, update_story_status

_STOPPED = ("failed", "blocked")


def node_release_agent(state: PipelineState) -> dict[str, Any]:
    if state.get("status") in _STOPPED:
        return state

    conn = db_conn(state)
    prompt = ""
    try:
        update_run_stage(conn, state["run_id"], "release-agent")
        conn.commit()
        prompt = build_release_prompt(state, release_evidence_gaps(state))
        try:
            result, parsed = run_agent_json(state, "release-agent", prompt)
        except ReplayGap as gap:
            # A run recorded before the release-agent existed: skip it, on record.
            log_agent(conn, state["run_id"], "release-agent", prompt,
                      f"skipped — {gap}", verdict="skipped")
            conn.commit()
            return {"release": {}}

        if parsed.get("error") == "Agent did not return valid JSON":
            log_agent(conn, state["run_id"], "release-agent", prompt, result.output,
                      verdict="blocked", duration_secs=result.duration_secs,
                      **usage_kwargs(result))
            conn.commit()
            return {"release_raw": result.output, "release": {}}

        notes = ReleaseOutput.model_validate(parsed)
        log_agent(conn, state["run_id"], "release-agent", prompt, result.output,
                  verdict=notes.verdict, duration_secs=result.duration_secs,
                  **usage_kwargs(result))
        conn.commit()
        spec = SpecOutput.model_validate(state["spec"])
        write_chain_artifact(state, "release", spec.title, notes)
        return {"release_raw": result.output, "release": parsed}
    except Exception as e:  # evidence, not the change: a gap at the gate, not a dead run
        log_agent(conn, state["run_id"], "release-agent", prompt, str(e), verdict="error")
        conn.commit()
        return {"release": {}}
    finally:
        conn.close()


def node_release(state: PipelineState) -> dict[str, Any]:
    """The operator approved Checkpoint 3: merge the story (release = merged PR).

    Released only once the merge landed. A merge that cannot land — a conflict, a
    failing required check on GitHub — BLOCKS the run with the reason; the main line
    is left as it was, and `factory retry` re-attempts the merge once it is fixed.
    """
    if state.get("status") in _STOPPED:
        return state
    merged, detail = merge_release(state)
    conn = db_conn(state)
    try:
        update_run_stage(conn, state["run_id"], "release")
        if not merged:
            error = f"Release approved, but the merge did not land: {detail}"
            update_story_status(conn, state["story_id"], "blocked")
            finish_run(conn, state["run_id"], "blocked", error=error)
            conn.commit()
            return {"status": "blocked", "error": error}
        update_story_status(conn, state["story_id"], "completed")
        finish_run(conn, state["run_id"], "completed")
        conn.commit()
    finally:
        conn.close()
    # The final package: the same evidence, now carrying the operator's release.
    write_trust_package(state)
    return {"status": "completed"}
