"""The pipeline's single agent-call boundary: run (or replay) an agent, parse its JSON.

Every node reaches a model through `run_agent_json` → `_run_or_replay`, so this
module is also where tests patch the boundary (`_run_or_replay`, or `run_agent`
one level lower) — patch it HERE, where the names are looked up.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from factory.adapters.opencode import AgentResult, run_agent
from factory.agent_config import tiers
from factory.agent_config.settings import settings
from factory.agent_config.tiers import resolve_model
from factory.domain.agent_output import parse_agent_json
from factory.domain.budget import Spend, budget_refusal, story_spend
from factory.pipeline.state import PipelineState
from factory.state.db import connect, get_agent_log, get_agent_log_by_stage, usage_rows


class BudgetExhausted(RuntimeError):
    """The user story has spent its cap (settings().budget): no further model call is made."""


def spend_so_far(state: PipelineState) -> Spend:
    """What this run's user story has spent across all its live runs (estimated)."""
    conn = db_conn(state)
    try:
        rows = usage_rows(conn, story_id=state.get("story_id"))
    finally:
        conn.close()
    return story_spend(rows, tiers.config().prices)


def budget_refusal_for(state: PipelineState) -> str | None:
    """Why this story may make no further model call (None if it may). A replay
    feeds frozen outputs and spends nothing, so it is never refused."""
    if state.get("replay_run_id") or not state.get("story_id"):
        return None
    spend = spend_so_far(state)
    cap = settings().budget.max_story_cost_usd
    if state.get("plan_id"):
        from factory.state.workflow import get_plan
        conn = db_conn(state)
        try:
            cap = min(cap, get_plan(conn, state["plan_id"]).budget_usd)
        finally:
            conn.close()
        if spend.unknown_calls:
            return "Batch usage is unknown; reconcile usage before spending more of its reservation"
    return budget_refusal(spend, cap)


class ReplayGap(RuntimeError):
    """A replayed run has no frozen output for this agent.

    Every other node treats it as a failure (a replay must re-drive what really
    happened). Agents added AFTER a run was recorded — the release-agent — catch
    it and skip, recorded as `skipped`, so old runs and eval cases keep replaying.
    """


def _extract_json(text: str) -> dict[str, Any]:
    """Extract JSON from agent output; synthesize a 'blocked' result on failure."""
    parsed = parse_agent_json(text)
    if parsed is not None:
        return parsed
    # No valid JSON — return a synthetic blocked response instead of crashing.
    return {
        "verdict": "blocked",
        "error": "Agent did not return valid JSON",
        "agent_response": text[:500].strip(),
    }


_JSON_REPAIR_SUFFIX = (
    "\n\n---\nYOUR PREVIOUS RESPONSE WAS NOT VALID JSON AND COULD NOT BE PARSED. "
    "Reply with ONLY the JSON object for this stage — no prose, no markdown fences, "
    "no commentary before or after. Begin with '{' and end with '}'."
)


def run_agent_json(
    state: PipelineState, agent_name: str, prompt: str, slot: str | None = None
) -> tuple[AgentResult, dict[str, Any]]:
    """Run an agent and parse its JSON, with ONE repair retry on a live run.

    Models occasionally wrap output in prose or fences that won't parse. Rather
    than dead-ending the whole run on a formatting slip, re-ask once for JSON-only.
    Replay runs skip the retry (their outputs are frozen) and fall through to the
    synthetic 'blocked' result, preserving existing replay behavior. The returned
    dict is the parsed object, or the synthetic 'blocked' shape `_extract_json`
    produces — so callers' existing off-script handling is unchanged.
    """
    if state.get("plan_id") and not state.get("replay_run_id") and not state.get("triggered_by") and slot is None:
        from factory.state.workflow import get_plan
        import json
        conn = db_conn(state)
        try:
            plan = get_plan(conn, state["plan_id"])
        finally:
            conn.close()
        prepared = {"spec-agent": plan.spec, "architect-agent": plan.architecture}.get(agent_name)
        if prepared:
            return AgentResult(agent=agent_name, output=json.dumps(prepared), duration_secs=0.0,
                               returncode=0, tokens_in=0, tokens_out=0, model_name="factory/prepared"), prepared
    _check_budget(state)
    result = _run_or_replay(state, agent_name, prompt, slot)
    parsed = parse_agent_json(result.output)
    if parsed is not None:
        return result, parsed
    if not state.get("replay_run_id"):
        _check_budget(state)  # the repair retry is a model call too
        result = _run_or_replay(state, agent_name, prompt + _JSON_REPAIR_SUFFIX, slot)
        reparsed = parse_agent_json(result.output)
        if reparsed is not None:
            return result, reparsed
    return result, _extract_json(result.output)


def _check_budget(state: PipelineState) -> None:
    """Before EVERY live model call: the story's $ cap (operator decision 2026-10-02)."""
    refusal = budget_refusal_for(state)
    if refusal:
        raise BudgetExhausted(refusal)


def _run_or_replay(
    state: PipelineState, agent_name: str, prompt: str, slot: str | None = None
) -> AgentResult:
    """Call the agent, or replay a stored output when replay_run_id is set.

    Replay reads the frozen output of the SAME agent from the original run's
    agent_logs, so the orchestration (gates, parsing, edges, materialization) is
    re-exercised with zero opencode calls and zero cost. `slot` (a task id, stored
    in stage_type) selects the right per-task coder output during replay.
    """
    replay_id = state.get("replay_run_id")
    if replay_id:
        conn = db_conn(state)
        try:
            if slot is not None:
                log = get_agent_log_by_stage(conn, replay_id, agent_name, slot)
            else:
                log = get_agent_log(conn, replay_id, agent_name)
        finally:
            conn.close()
        if log is None:
            raise ReplayGap(
                f"Cannot replay: no stored output for '{agent_name}' in run {replay_id}"
            )
        return AgentResult(
            agent=agent_name,
            output=log.get("output_text") or "",
            duration_secs=0.0,
            returncode=0,
            tokens_in=log.get("tokens_in"),
            tokens_out=log.get("tokens_out"),
            cost_usd=log.get("cost_usd"),
            model_name=log.get("model_name"),
            agent_prompt_hash=log.get("agent_prompt_hash"),
        )
    # Tier policy: frontier models for the thinking stages, a cheap model for the
    # coder — escalated to frontier on a remediation retry. attempt_number is the
    # coder's per-task counter; it's harmless for the (non-escalating) others.
    model, _tier = resolve_model(agent_name, state.get("attempt_number", 1))
    if state.get("plan_id"):
        from factory.state.workflow import get_plan
        conn = db_conn(state)
        try:
            plan = get_plan(conn, state["plan_id"])
        finally:
            conn.close()
        prompt += "\n\n## Approved batch reservation (binding)\n" + plan.model_dump_json() + (
            "\nEvery write, including tests and manifests, must fit these file resources. "
            "Do not broaden semantic impact. Report a blocking decision if the plan needs revision.")
    return run_agent(agent_name, prompt, cwd=state.get("opencode_cwd"), model=model)


def usage_kwargs(result: AgentResult) -> dict[str, Any]:
    """Build the cost/provenance kwargs for log_agent from an agent result."""
    return {
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "cost_usd": result.cost_usd,
        "model_name": result.model_name,
        "agent_prompt_hash": result.agent_prompt_hash,
    }


def db_conn(state: PipelineState) -> sqlite3.Connection:
    """Get a DB connection from state (the caller closes it)."""
    return connect(state["db_path"])
