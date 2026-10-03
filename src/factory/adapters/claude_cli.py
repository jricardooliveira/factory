"""Run an agent through the local Claude Code CLI (`claude -p`) instead of opencode.

Chosen per call by the model id: `claude/<model>` (see agents/tiers.toml
[claude_tiers]); `opencode.run_agent` routes here. The CLI uses its own login.
Same contract as opencode: the agent's .md body is the system prompt and every
tool is disabled, so the agent can only answer with text.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from factory.adapters.result import AgentResult, _default_timeout, _hash_agent_definition

PREFIX = "claude/"
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def _agent_body(agent_name: str, cwd: str | None) -> str | None:
    """The agent's .md minus frontmatter, resolved from cwd as opencode would."""
    start = Path(cwd or ".").resolve()
    for directory in [start, *start.parents]:
        candidate = directory / ".opencode" / "agents" / f"{agent_name}.md"
        if candidate.is_file():
            return _FRONTMATTER.sub("", candidate.read_text(encoding="utf-8"), count=1)
    return None


def run_agent(
    agent_name: str, prompt: str, *, cwd: str | None, model: str, timeout: int | None
) -> AgentResult:
    timeout = timeout if timeout is not None else _default_timeout()
    start = time.monotonic()
    prompt_hash = _hash_agent_definition(agent_name, cwd)

    def failed(output: str) -> AgentResult:
        return AgentResult(agent_name, output, round(time.monotonic() - start, 2), -1,
                           model_name=model, agent_prompt_hash=prompt_hash)

    system_prompt = _agent_body(agent_name, cwd)
    if system_prompt is None:
        return failed(f"ERROR: no .opencode/agents/{agent_name}.md above {cwd}")
    try:
        proc = subprocess.run(
            [
                "claude", "-p",
                "--output-format", "json",
                "--model", model.removeprefix(PREFIX),
                "--system-prompt", system_prompt,
                "--tools", "",
                # Isolation: the product repo's .claude settings, hooks and MCP stay out.
                "--setting-sources", "",
                "--no-session-persistence",
            ],
            # stdin, not argv: a prompt can exceed the 128 KiB per-argument limit.
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return failed(f"ERROR: Agent timed out after {timeout} seconds")
    except FileNotFoundError:
        return failed("ERROR: 'claude' command not found. Install Claude Code: "
                      "https://claude.com/claude-code")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        detail = (proc.stderr or proc.stdout).strip()[:500]
        return failed(f"ERROR: claude exited {proc.returncode}: {detail}")
    usage = data.get("usage") or {}
    tokens_in = sum(usage.get(k) or 0 for k in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    error = data.get("is_error") or proc.returncode != 0
    output = data.get("result") or ""
    return AgentResult(
        agent=agent_name,
        output=f"ERROR: {output}" if error else output,
        duration_secs=round(time.monotonic() - start, 2),
        returncode=(proc.returncode or 1) if error else 0,
        tokens_in=tokens_in if usage else None,
        tokens_out=usage.get("output_tokens"),
        cost_usd=data.get("total_cost_usd"),
        model_name=model,
        agent_prompt_hash=prompt_hash,
    )
