"""The interview-agent through the Claude Agent SDK (optional extra: `pip install .[claude]`).

The SDK drives the local `claude` CLI and its login. It is imported lazily so the
factory runs without it; `available()` says whether it can be used at all.
"""

from __future__ import annotations

import asyncio
import shutil
import time

from factory.adapters.opencode import AgentResult, _default_timeout

AGENT = "interview-agent"


def available() -> bool:
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False
    return shutil.which("claude") is not None


def sdk_model(model: str) -> str:
    # tiers.toml names Claude models as opencode reaches them, through Requesty
    # ("requesty/claude-opus-5-5"); the SDK talks to Anthropic directly and wants
    # the bare model id.
    return model.removeprefix("requesty/")


def run_prompt(
    system_prompt: str, prompt: str, *, model: str, cwd: str | None, timeout: int | None
) -> AgentResult:
    """One turn, no tools: the agent proposes text and can neither read nor write files."""
    start = time.monotonic()

    def failed(output: str) -> AgentResult:
        return AgentResult(AGENT, output, time.monotonic() - start, 1,
                           model_name=f"claude-sdk/{model}")

    try:
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=model,
            tools=[],
            max_turns=1,
            cwd=cwd,
            # Isolation: the product repo's CLAUDE.md, hooks and settings stay out.
            setting_sources=[],
        )

        async def last_result():
            final = None
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    final = message
            return final

        final = asyncio.run(asyncio.wait_for(last_result(), timeout or _default_timeout()))
    except Exception as exc:  # any SDK/CLI/timeout failure is a failed call, never a crash
        return failed(f"ERROR: {type(exc).__name__}: {exc}")
    if final is None:
        return failed("ERROR: no result message from the Claude Agent SDK")
    usage = final.usage or {}
    tokens_in = sum(usage.get(k) or 0 for k in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return AgentResult(
        AGENT,
        final.result or "",
        time.monotonic() - start,
        1 if final.is_error else 0,
        tokens_in=tokens_in if usage else None,
        tokens_out=usage.get("output_tokens"),
        cost_usd=final.total_cost_usd,
        model_name=f"claude-sdk/{model}",
    )
