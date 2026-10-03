"""A formatting slip (non-JSON output) gets one repair retry before 'blocked'.

This exercises the LIVE agent-call path (which replay fixtures can't, since their
outputs are frozen), so it mocks the single call boundary `_run_or_replay`.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from factory.pipeline import agent_calls
from factory.adapters.opencode import AgentResult


def _ar(output: str) -> AgentResult:
    return AgentResult(agent="spec-agent", output=output, duration_secs=0.0, returncode=0)


class JsonRepairTests(unittest.TestCase):
    def test_valid_json_first_try_does_not_retry(self) -> None:
        with patch.object(agent_calls, "_run_or_replay", return_value=_ar('{"verdict": "pass"}')) as m:
            _result, parsed = agent_calls.run_agent_json({}, "spec-agent", "p")
        self.assertEqual(parsed.get("verdict"), "pass")
        self.assertEqual(m.call_count, 1)

    def test_malformed_then_valid_on_repair(self) -> None:
        seq = [_ar("Sure! Here is the spec you asked for."), _ar('{"verdict": "pass"}')]
        with patch.object(agent_calls, "_run_or_replay", side_effect=seq) as m:
            _result, parsed = agent_calls.run_agent_json({}, "spec-agent", "p")
        self.assertEqual(parsed.get("verdict"), "pass")
        self.assertEqual(m.call_count, 2)
        # The retry prompt carried the repair instruction.
        self.assertIn("NOT VALID JSON", m.call_args_list[1].args[2])

    def test_malformed_twice_returns_synthetic_blocked(self) -> None:
        seq = [_ar("no json here"), _ar("still no json")]
        with patch.object(agent_calls, "_run_or_replay", side_effect=seq) as m:
            result, parsed = agent_calls.run_agent_json({}, "spec-agent", "p")
        self.assertEqual(parsed.get("error"), "Agent did not return valid JSON")
        self.assertEqual(m.call_count, 2)
        # The surfaced output is the latest attempt's, for diagnosis.
        self.assertIn("still no json", result.output)

    def test_replay_mode_never_retries(self) -> None:
        # Replay outputs are frozen — a second call would be meaningless (and would
        # re-fetch the same stored text), so repair is skipped under replay.
        with patch.object(agent_calls, "_run_or_replay", return_value=_ar("no json")) as m:
            _result, parsed = agent_calls.run_agent_json({"replay_run_id": 7}, "spec-agent", "p")
        self.assertEqual(parsed.get("error"), "Agent did not return valid JSON")
        self.assertEqual(m.call_count, 1)


class ProviderFailureTests(unittest.TestCase):
    """A call that FAILED is not an agent that answered badly (run 1, agent_logs
    8-28: a session limit became a paid repair call and "Agent went off-script")."""

    def _failed(self, output: str, returncode: int = 1) -> AgentResult:
        return AgentResult(agent="coder-agent", output=output, duration_secs=2.0,
                           returncode=returncode)

    def test_a_failed_call_raises_with_the_providers_message_and_no_repair(self) -> None:
        limit = self._failed("ERROR: You've hit your session limit · resets 4am")
        with patch.object(agent_calls, "_run_or_replay", return_value=limit) as m:
            with self.assertRaises(agent_calls.ProviderUnavailable) as caught:
                agent_calls.run_agent_json({"run_id": 7}, "coder-agent", "p")
        self.assertEqual(m.call_count, 1, "no JSON-repair call on a provider failure")
        message = str(caught.exception)
        self.assertIn("the model call failed: You've hit your session limit · resets 4am",
                      message)
        self.assertNotIn("ERROR:", message)
        self.assertIn("factory retry 7", message)

    def test_json_inside_a_failed_call_is_never_taken_as_the_answer(self) -> None:
        # A provider's error body is JSON too.
        body = self._failed('ERROR: 529 {"type": "error", "error": {"type": "overloaded"}}')
        with patch.object(agent_calls, "_run_or_replay", return_value=body):
            with self.assertRaises(agent_calls.ProviderUnavailable):
                agent_calls.run_agent_json({}, "coder-agent", "p")

    def test_a_failed_repair_call_is_a_provider_failure_too(self) -> None:
        seq = [_ar("no json here"), self._failed("ERROR: Agent timed out after 600 seconds", -1)]
        with patch.object(agent_calls, "_run_or_replay", side_effect=seq):
            with self.assertRaises(agent_calls.ProviderUnavailable) as caught:
                agent_calls.run_agent_json({}, "coder-agent", "p")
        self.assertIn("timed out after 600 seconds", str(caught.exception))

    def test_an_empty_failure_names_the_exit_code(self) -> None:
        with patch.object(agent_calls, "_run_or_replay", return_value=self._failed("", 3)):
            with self.assertRaises(agent_calls.ProviderUnavailable) as caught:
                agent_calls.run_agent_json({}, "coder-agent", "p")
        self.assertIn("exit 3", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
