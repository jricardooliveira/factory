"""Tests for opencode CLI invocation."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.adapters.opencode import (
    _extract_usage_from_json_stream,
    _hash_agent_definition,
    run_agent,
)


class OpencodeClientTests(unittest.TestCase):
    """The opencode wrapper should pass prompts safely."""

    def test_run_agent_separates_dash_prefixed_prompt_from_options(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"type":"text","part":{"text":"{}"}}\n',
            stderr="",
        )

        with patch("factory.adapters.opencode.subprocess.run", return_value=completed) as run:
            run_agent("spec-agent", "-h", cwd="/tmp/project")

        argv = run.call_args.args[0]
        self.assertIn("--", argv)
        separator_index = argv.index("--")
        self.assertEqual(argv[separator_index + 1], "-h")
        self.assertEqual(argv[-1], "-h")

    def test_timeout_is_configurable_via_env(self) -> None:
        import os

        from factory.adapters.opencode import _default_timeout

        original = os.environ.get("FACTORY_AGENT_TIMEOUT")
        try:
            os.environ["FACTORY_AGENT_TIMEOUT"] = "900"
            self.assertEqual(_default_timeout(), 900)
            os.environ["FACTORY_AGENT_TIMEOUT"] = "not-a-number"
            self.assertEqual(_default_timeout(), 600)  # falls back to default
            del os.environ["FACTORY_AGENT_TIMEOUT"]
            self.assertEqual(_default_timeout(), 600)
        finally:
            if original is None:
                os.environ.pop("FACTORY_AGENT_TIMEOUT", None)
            else:
                os.environ["FACTORY_AGENT_TIMEOUT"] = original

    def test_run_agent_passes_model_override(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("factory.adapters.opencode.subprocess.run", return_value=completed) as run:
            run_agent("coder-agent", "hi", model="anthropic/claude-opus-4-8")
        argv = run.call_args.args[0]
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "anthropic/claude-opus-4-8")

    def test_run_agent_omits_model_flag_when_unset(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("factory.adapters.opencode.subprocess.run", return_value=completed) as run:
            run_agent("coder-agent", "hi")
        self.assertNotIn("--model", run.call_args.args[0])

    def test_run_agent_passes_configured_timeout(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("factory.adapters.opencode.subprocess.run", return_value=completed) as run:
            run_agent("spec-agent", "hi")
        self.assertEqual(run.call_args.kwargs["timeout"], 600)


class UsageExtractionTests(unittest.TestCase):
    """Token/cost/model provenance is scanned out of the event stream."""

    def test_returns_last_usage_bearing_message(self) -> None:
        stream = "\n".join(
            [
                json.dumps({"type": "text", "part": {"text": "hi"}}),
                json.dumps(
                    {
                        "type": "message.updated",
                        "properties": {
                            "info": {
                                "role": "assistant",
                                "modelID": "claude-x",
                                "providerID": "anthropic",
                                "cost": 0.04,
                                "tokens": {"input": 10, "output": 5},
                            }
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "message.updated",
                        "properties": {
                            "info": {
                                "role": "assistant",
                                "modelID": "claude-x",
                                "providerID": "anthropic",
                                "cost": 0.09,
                                "tokens": {"input": 20, "output": 12},
                            }
                        },
                    }
                ),
            ]
        )
        usage = _extract_usage_from_json_stream(stream)
        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertEqual(usage["cost"], 0.09)
        self.assertEqual(usage["tokens"]["input"], 20)
        self.assertEqual(usage["modelID"], "claude-x")

    def test_returns_none_when_no_usage_present(self) -> None:
        stream = json.dumps({"type": "text", "part": {"text": "hi"}})
        self.assertIsNone(_extract_usage_from_json_stream(stream))

    def test_ignores_malformed_lines(self) -> None:
        stream = "not json\n" + json.dumps({"type": "text", "part": {"text": "x"}})
        self.assertIsNone(_extract_usage_from_json_stream(stream))


class AgentDefinitionHashTests(unittest.TestCase):
    """The agent .md used for a run is hashed for provenance."""

    def test_resolves_and_hashes_agent_definition_walking_up(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            agents = Path(root) / ".opencode" / "agents"
            agents.mkdir(parents=True)
            (agents / "spec-agent.md").write_text("you are spec")
            nested = Path(root) / "projects" / "PROJ-001"
            nested.mkdir(parents=True)

            h = _hash_agent_definition("spec-agent", str(nested))
            self.assertIsNotNone(h)
            assert h is not None
            self.assertEqual(len(h), 16)

    def test_returns_none_for_missing_agent(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(_hash_agent_definition("nope", root))
