"""Wrapper for calling opencode agents via CLI."""

from __future__ import annotations

import json
import subprocess
import time

from factory.adapters import claude_cli
# Re-exported: callers and tests import these from here.
from factory.adapters.result import (  # noqa: F401
    AgentResult,
    _default_timeout,
    _hash_agent_definition,
)


def _extract_text_from_json_stream(raw: str) -> str:
    """Extract text parts from opencode's --format json event stream."""
    texts: list[str] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if event.get("type") == "text":
                part = event.get("part", {})
                text = part.get("text", "")
                if text:
                    texts.append(text)
            elif event.get("type") == "error":
                err = event.get("error", {})
                msg = err.get("data", {}).get("message", str(err))
                texts.append(f"ERROR: {msg}")
        except json.JSONDecodeError:
            continue
    return "".join(texts)


def _find_usage(obj: object, found: list[dict]) -> None:
    """Recursively collect objects carrying an AssistantMessage-style usage shape.

    The opencode `--format json` CLI flattens SDK events into its own envelopes,
    so rather than key on a specific event type we scan for any nested object that
    has both `tokens.{input,output}` and a `modelID`. The final assistant message
    carries cumulative totals, so callers take the last match.
    """
    if isinstance(obj, dict):
        tokens = obj.get("tokens")
        if (
            isinstance(tokens, dict)
            and "input" in tokens
            and "output" in tokens
            and obj.get("modelID")
        ):
            found.append(obj)
        for value in obj.values():
            _find_usage(value, found)
    elif isinstance(obj, list):
        for item in obj:
            _find_usage(item, found)


def _step_finish_usage(event: object) -> dict | None:
    """The usage on one `step_finish` event — the shape opencode actually emits.

    Observed live (2026-10-02): `{"type": "step_finish", "part": {"tokens":
    {"input", "output", "reasoning", ...}, "cost"}}`, with NO modelID. Requiring a
    modelID (the message-level shape above) is why every agent_logs row stored
    NULL usage and the `$` budget never bound.
    """
    if not isinstance(event, dict) or event.get("type") != "step_finish":
        return None
    part = event.get("part")
    tokens = part.get("tokens") if isinstance(part, dict) else None
    if not isinstance(tokens, dict) or "input" not in tokens or "output" not in tokens:
        return None
    return part


def _extract_usage_from_json_stream(raw: str) -> dict | None:
    """Usage for the whole call: `{"tokens": {"input", "output"}, "cost", ...}` or None.

    A message-level object carrying cumulative totals wins (the last one). Else the
    `step_finish` parts are SUMMED — one per step of a multi-step run — with
    reasoning tokens counted as output, since they are billed as output.
    """
    found: list[dict] = []
    steps: list[dict] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        _find_usage(event, found)
        step = _step_finish_usage(event)
        if step is not None:
            steps.append(step)
    if found:
        return found[-1]
    if not steps:
        return None
    costs = [s.get("cost") for s in steps if isinstance(s.get("cost"), (int, float))]
    return {
        "tokens": {
            "input": sum(int(s["tokens"].get("input") or 0) for s in steps),
            "output": sum(int(s["tokens"].get("output") or 0)
                          + int(s["tokens"].get("reasoning") or 0) for s in steps),
        },
        # None when no step reported a cost: unknown is not zero.
        "cost": sum(costs) if costs else None,
    }


def run_agent(
    agent_name: str,
    prompt: str,
    cwd: str | None = None,
    model: str | None = None,
    timeout: int | None = None,
) -> AgentResult:
    """Call `opencode run --agent <name> --format json <prompt>`.

    Uses JSON format for clean output parsing. When ``model`` is given it is
    passed as ``--model provider/model``, overriding the agent's frontmatter so
    the orchestrator's tier policy (see ``agent_config.tiers``) — including
    cheap-first/escalate-on-retry — is the single source of truth. ``timeout``
    (seconds) overrides ``FACTORY_AGENT_TIMEOUT`` for this one call.
    """
    if model and model.startswith("claude/"):
        # The operator's claude runner (agents/tiers.toml [claude_tiers]).
        return claude_cli.run_agent(agent_name, prompt, cwd=cwd, model=model, timeout=timeout)
    timeout = timeout if timeout is not None else _default_timeout()
    start = time.monotonic()
    model_args = ["--model", model] if model else []
    try:
        result = subprocess.run(
            [
                "opencode", "run",
                "--agent", agent_name,
                "--format", "json",
                "--dangerously-skip-permissions",
                *model_args,
                "--",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            # `opencode run` READS STDIN when it is not a terminal and waits for
            # EOF before starting. Inherited from a background job, cron, CI or the
            # board's TUI worker, that pipe never closes and every call hangs until
            # the agent timeout. The orchestrator never talks to a child on stdin.
            stdin=subprocess.DEVNULL,
        )
        duration = time.monotonic() - start
        output = _extract_text_from_json_stream(result.stdout)
        if not output and result.stderr:
            output = f"ERROR: {result.stderr.strip()}"
        if not output and result.returncode != 0:
            output = f"ERROR: opencode exited with code {result.returncode}"

        usage = _extract_usage_from_json_stream(result.stdout)
        tokens_in = tokens_out = None
        cost_usd = model_name = None
        if usage:
            tokens = usage.get("tokens", {})
            tokens_in = tokens.get("input")
            tokens_out = tokens.get("output")
            cost_usd = usage.get("cost")
            provider = usage.get("providerID")
            observed = usage.get("modelID")
            model_name = f"{provider}/{observed}" if provider and observed else observed
        # The stream may not name the model (step_finish does not): record the one
        # this call asked for rather than nothing.
        model_name = model_name or model

        return AgentResult(
            agent=agent_name,
            output=output,
            duration_secs=round(duration, 2),
            returncode=result.returncode,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            model_name=model_name,
            agent_prompt_hash=_hash_agent_definition(agent_name, cwd),
        )
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return AgentResult(
            agent=agent_name,
            output=f"ERROR: Agent timed out after {timeout} seconds",
            duration_secs=round(duration, 2),
            returncode=-1,
            agent_prompt_hash=_hash_agent_definition(agent_name, cwd),
        )
    except FileNotFoundError:
        duration = time.monotonic() - start
        return AgentResult(
            agent=agent_name,
            output="ERROR: 'opencode' command not found. Install it from https://opencode.ai",
            duration_secs=round(duration, 2),
            returncode=-1,
            agent_prompt_hash=_hash_agent_definition(agent_name, cwd),
        )
