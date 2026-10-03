"""Desktop notifications must never carry an operator-unsafe payload.

`notify` hands run titles to AppleScript, so anything an agent wrote into a story
title reaches a shell-adjacent interpreter. Moved out of the old CLI queue suite to
mirror `factory.adapters.notify`.
"""

from __future__ import annotations

import unittest

from factory.adapters.notify import _clean


class NotifySanitizationTests(unittest.TestCase):
    def test_strips_applescript_injection_chars(self) -> None:
        dirty = 'Run #1 "; do shell script "rm -rf /" \\ \n done'
        cleaned = _clean(dirty)
        self.assertNotIn('"', cleaned)
        self.assertNotIn("\\", cleaned)
        self.assertNotIn("\n", cleaned)
        # Harmless content survives.
        self.assertIn("Run #1", cleaned)

    def test_truncates_long_messages(self) -> None:
        self.assertLessEqual(len(_clean("a" * 500)), 180)


class LinuxNotifyTests(unittest.TestCase):
    """notify-send on Linux: the same opt-in, an argv (no script to inject into)."""

    def _notify(self, *, env: str, which: str | None) -> tuple[bool, list]:
        from unittest.mock import patch

        from factory.adapters import notify as mod

        with patch.dict("os.environ", {"FACTORY_NOTIFY": env}), \
                patch.object(mod.sys, "platform", "linux"), \
                patch.object(mod.shutil, "which", lambda name: which if name == "notify-send" else None), \
                patch.object(mod.subprocess, "run") as run:
            sent = mod.notify("Run #3", "parked at Checkpoint 2")
        return sent, run.call_args_list

    def test_opted_in_with_notify_send_dispatches(self) -> None:
        sent, calls = self._notify(env="1", which="/usr/bin/notify-send")
        self.assertTrue(sent)
        self.assertEqual(calls[0].args[0], ["notify-send", "Run #3", "parked at Checkpoint 2"])

    def test_not_opted_in_stays_silent(self) -> None:
        sent, calls = self._notify(env="", which="/usr/bin/notify-send")
        self.assertFalse(sent)
        self.assertEqual(calls, [])

    def test_without_notify_send_is_a_no_op(self) -> None:
        sent, calls = self._notify(env="1", which=None)
        self.assertFalse(sent)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
