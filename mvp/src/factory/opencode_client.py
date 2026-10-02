"""Wrapper for calling opencode agents via CLI."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


def _default_timeout() -> int:
    """Per-agent subprocess timeout in seconds (override via FACTORY_AGENT_TIMEOUT)."""
    try:
        return max(1, int(os.environ.get("FACTORY_AGENT_TIMEOUT", "600")))
    except ValueError:
        return 600


@dataclass
class AgentResult:
    agent: str
    output: str
    duration_secs: float
    returncode: int
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    model_name: str | None = None
    agent_prompt_hash: str | None = None

    @property
    def success(self) -> bool:
        return self.returncode == 0


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


def _extract_usage_from_json_stream(raw: str) -> dict | None:
    """Return the last usage-bearing object in the event stream, if any."""
    found: list[dict] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            _find_usage(json.loads(line), found)
        except json.JSONDecodeError:
            continue
    return found[-1] if found else None


def _hash_agent_definition(agent_name: str, cwd: str | None) -> str | None:
    """Hash the .opencode/agents/<name>.md that opencode would resolve from cwd.

    Walks up the directory tree from cwd (mirroring opencode's config discovery)
    and hashes the first matching agent definition. Returns None if not found.
    """
    start = Path(cwd) if cwd else Path.cwd()
    try:
        start = start.resolve()
    except OSError:
        return None
    for directory in [start, *start.parents]:
        candidate = directory / ".opencode" / "agents" / f"{agent_name}.md"
        if candidate.is_file():
            try:
                data = candidate.read_bytes()
            except OSError:
                return None
            return hashlib.sha256(data).hexdigest()[:16]
    return None


def run_agent(
    agent_name: str, prompt: str, cwd: str | None = None, model: str | None = None
) -> AgentResult:
    """Call `opencode run --agent <name> --format json <prompt>`.

    Uses JSON format for clean output parsing. When ``model`` is given it is
    passed as ``--model provider/model``, overriding the agent's frontmatter so
    the orchestrator's tier policy (see ``model_tiers``) — including
    cheap-first/escalate-on-retry — is the single source of truth.
    """
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
            timeout=_default_timeout(),
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
            model = usage.get("modelID")
            model_name = f"{provider}/{model}" if provider else model

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
            output=f"ERROR: Agent timed out after {_default_timeout()} seconds",
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
