"""A formatting slip (non-JSON output) gets one repair retry before 'blocked'.

This exercises the LIVE agent-call path (which replay fixtures can't, since their
outputs are frozen), so it mocks the single call boundary `_run_or_replay`.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from factory import pipeline
from factory.opencode_client import AgentResult


def _ar(output: str) -> AgentResult:
    return AgentResult(agent="spec-agent", output=output, duration_secs=0.0, returncode=0)


class JsonRepairTests(unittest.TestCase):
    def test_valid_json_first_try_does_not_retry(self) -> None:
        with patch.object(pipeline, "_run_or_replay", return_value=_ar('{"verdict": "pass"}')) as m:
            _result, parsed = pipeline._run_agent_json({}, "spec-agent", "p")
        self.assertEqual(parsed.get("verdict"), "pass")
        self.assertEqual(m.call_count, 1)

    def test_malformed_then_valid_on_repair(self) -> None:
        seq = [_ar("Sure! Here is the spec you asked for."), _ar('{"verdict": "pass"}')]
        with patch.object(pipeline, "_run_or_replay", side_effect=seq) as m:
            _result, parsed = pipeline._run_agent_json({}, "spec-agent", "p")
        self.assertEqual(parsed.get("verdict"), "pass")
        self.assertEqual(m.call_count, 2)
        # The retry prompt carried the repair instruction.
        self.assertIn("NOT VALID JSON", m.call_args_list[1].args[2])

    def test_malformed_twice_returns_synthetic_blocked(self) -> None:
        seq = [_ar("no json here"), _ar("still no json")]
        with patch.object(pipeline, "_run_or_replay", side_effect=seq) as m:
            result, parsed = pipeline._run_agent_json({}, "spec-agent", "p")
        self.assertEqual(parsed.get("error"), "Agent did not return valid JSON")
        self.assertEqual(m.call_count, 2)
        # The surfaced output is the latest attempt's, for diagnosis.
        self.assertIn("still no json", result.output)

    def test_replay_mode_never_retries(self) -> None:
        # Replay outputs are frozen — a second call would be meaningless (and would
        # re-fetch the same stored text), so repair is skipped under replay.
        with patch.object(pipeline, "_run_or_replay", return_value=_ar("no json")) as m:
            _result, parsed = pipeline._run_agent_json({"replay_run_id": 7}, "spec-agent", "p")
        self.assertEqual(parsed.get("error"), "Agent did not return valid JSON")
        self.assertEqual(m.call_count, 1)


if __name__ == "__main__":
    unittest.main()
