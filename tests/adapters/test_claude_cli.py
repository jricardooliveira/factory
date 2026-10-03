"""The `claude` CLI runner: a `claude/<model>` id routes an agent call to `claude -p`."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.adapters.opencode import run_agent

AGENT_MD = "---\ndescription: x\nmodel: openai/gpt\ntools:\n  write: false\n---\nYou are the spec agent.\n"


def _done(payload: dict, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, json.dumps(payload), "")


class ClaudeCliTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        (self.repo / ".opencode" / "agents").mkdir(parents=True)
        (self.repo / ".opencode" / "agents" / "spec-agent.md").write_text(AGENT_MD)

    def _run(self, completed=None, side_effect=None):
        with patch("factory.adapters.claude_cli.subprocess.run",
                   return_value=completed, side_effect=side_effect) as run:
            result = run_agent("spec-agent", "-h build it", cwd=str(self.repo),
                               model="claude/claude-opus-5-5")
        return result, run

    def test_runs_claude_print_with_the_agent_body_as_system_prompt_and_no_tools(self) -> None:
        result, run = self._run(_done({
            "result": '{"ok": true}', "is_error": False, "total_cost_usd": 0.25,
            "usage": {"input_tokens": 100, "cache_read_input_tokens": 20,
                      "cache_creation_input_tokens": 5, "output_tokens": 40},
        }))
        argv, kwargs = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(argv[:2], ["claude", "-p"])
        self.assertEqual(argv[argv.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(argv[argv.index("--system-prompt") + 1], "You are the spec agent.\n")
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        # The prompt goes on stdin: never parsed as a flag, never hits the argv size limit.
        self.assertEqual(kwargs["input"], "-h build it")
        self.assertNotIn("-h build it", argv)
        self.assertEqual(kwargs["cwd"], str(self.repo))
        self.assertTrue(result.success)
        self.assertEqual(result.output, '{"ok": true}')
        self.assertEqual((result.tokens_in, result.tokens_out), (125, 40))
        self.assertEqual(result.cost_usd, 0.25)
        self.assertEqual(result.model_name, "claude/claude-opus-5-5")
        self.assertIsNotNone(result.agent_prompt_hash)

    def test_an_error_result_is_a_failed_call(self) -> None:
        result, _ = self._run(_done({"result": "Not logged in", "is_error": True}, 1))
        self.assertFalse(result.success)
        self.assertIn("Not logged in", result.output)

    def test_unparseable_output_is_a_failed_call_not_a_crash(self) -> None:
        result, _ = self._run(subprocess.CompletedProcess([], 0, "garbage", "boom"))
        self.assertFalse(result.success)
        self.assertTrue(result.output.startswith("ERROR"))

    def test_missing_cli_and_timeout_are_failed_calls(self) -> None:
        result, _ = self._run(side_effect=FileNotFoundError())
        self.assertIn("'claude' command not found", result.output)
        result, _ = self._run(side_effect=subprocess.TimeoutExpired("claude", 1))
        self.assertIn("timed out", result.output)
        self.assertFalse(result.success)

    def test_a_missing_agent_definition_refuses_without_calling(self) -> None:
        with patch("factory.adapters.claude_cli.subprocess.run") as run:
            result = run_agent("ghost-agent", "x", cwd=str(self.repo),
                               model="claude/claude-opus-5-5")
        run.assert_not_called()
        self.assertFalse(result.success)
        self.assertIn("ghost-agent", result.output)


if __name__ == "__main__":
    unittest.main()
