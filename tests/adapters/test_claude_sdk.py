"""The Claude Agent SDK adapter, offline.

`claude-agent-sdk` is an optional extra and is NOT installed in the dev venv, so a
fake `claude_agent_sdk` module is put in `sys.modules`: the adapter imports it
lazily, so no test can reach a model whether or not the real SDK is present.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from factory.adapters import claude_sdk
from factory.adapters.opencode import AgentResult
from factory.state.db import init_db
from factory.workspace.projects import create_project


@dataclass
class _Options:
    system_prompt: str | None = None
    model: str | None = None
    tools: list[str] | None = None
    max_turns: int | None = None
    cwd: str | None = None
    setting_sources: list[str] | None = None


@dataclass
class _Result:
    is_error: bool = False
    result: str | None = None
    total_cost_usd: float | None = None
    usage: dict[str, Any] | None = field(default=None)


def _fake_sdk(messages: list[Any] | Exception) -> tuple[types.ModuleType, list[dict]]:
    calls: list[dict] = []

    async def query(*, prompt, options):
        calls.append({"prompt": prompt, "options": options})
        if isinstance(messages, Exception):
            raise messages
        for message in messages:
            yield message

    module = types.ModuleType("claude_agent_sdk")
    module.query = query
    module.ClaudeAgentOptions = _Options
    module.ResultMessage = _Result
    return module, calls


class ClaudeSdkTests(unittest.TestCase):
    def _run(self, messages):
        module, calls = _fake_sdk(messages)
        with patch.dict(sys.modules, {"claude_agent_sdk": module}):
            result = claude_sdk.run_prompt(
                "SYSTEM", "PROMPT", model="claude-opus-5-5", cwd="/tmp/p", timeout=5
            )
        return result, calls

    def test_one_turn_with_no_tools_and_no_filesystem_settings(self) -> None:
        usage = {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 7}
        result, calls = self._run(
            ["noise", _Result(result='{"done": true}', total_cost_usd=0.02, usage=usage)]
        )

        options = calls[0]["options"]
        self.assertEqual(calls[0]["prompt"], "PROMPT")
        self.assertEqual(options.system_prompt, "SYSTEM")
        self.assertEqual(options.model, "claude-opus-5-5")
        self.assertEqual(options.tools, [])
        self.assertEqual(options.max_turns, 1)
        self.assertEqual(options.setting_sources, [])
        self.assertEqual(options.cwd, "/tmp/p")
        self.assertTrue(result.success)
        self.assertEqual(result.agent, "interview-agent")
        self.assertEqual(result.output, '{"done": true}')
        self.assertEqual((result.tokens_in, result.tokens_out), (15, 7))
        self.assertEqual(result.cost_usd, 0.02)
        self.assertEqual(result.model_name, "claude-sdk/claude-opus-5-5")

    def test_an_error_result_an_exception_or_no_result_is_a_failed_call(self) -> None:
        for messages in ([_Result(is_error=True, result="overloaded")], RuntimeError("boom"), []):
            result, _ = self._run(messages)
            self.assertFalse(result.success, messages)

    def test_sdk_model_strips_the_requesty_provider_prefix(self) -> None:
        self.assertEqual(claude_sdk.sdk_model("requesty/claude-opus-5-5"), "claude-opus-5-5")
        self.assertEqual(claude_sdk.sdk_model("claude-opus-5-5"), "claude-opus-5-5")
        self.assertEqual(claude_sdk.sdk_model("claude/claude-opus-5-5"), "claude-opus-5-5")

    def test_available_needs_the_module_and_the_claude_cli(self) -> None:
        module, _ = _fake_sdk([])
        with patch.dict(sys.modules, {"claude_agent_sdk": module}):
            with patch("factory.adapters.claude_sdk.shutil.which", return_value="/bin/claude"):
                self.assertTrue(claude_sdk.available())
            with patch("factory.adapters.claude_sdk.shutil.which", return_value=None):
                self.assertFalse(claude_sdk.available())
        with patch.dict(sys.modules, {"claude_agent_sdk": None}):
            with patch("factory.adapters.claude_sdk.shutil.which", return_value="/bin/claude"):
                self.assertFalse(claude_sdk.available())


if __name__ == "__main__":
    unittest.main()


class TestEnvironmentNeverReachesTheSdk(unittest.TestCase):
    def test_a_test_that_patches_only_opencode_never_calls_the_sdk(self) -> None:
        # The [claude] extra + `claude` on PATH make "auto" pick the SDK: a live call
        # from any test that patched only run_agent. tests/conftest.py pins opencode.
        from factory.runs import interview

        self.assertEqual(os.environ.get("FACTORY_INTERVIEW_ENGINE"), "opencode")
        with patch.object(interview, "claude_sdk_available", return_value=True), \
             patch.object(interview, "claude_sdk_run") as sdk, \
             patch.object(interview, "run_agent") as opencode:
            opencode.return_value = AgentResult("interview-agent", '{"done": true}', 0.1, 0)
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            db_file = Path(tmp.name) / "factory.db"
            init_db(db_file)
            project = create_project(db_file, home=Path(tmp.name), slug="shop")
            interview.next_turn(project, [], db_path=db_file)
        sdk.assert_not_called()
