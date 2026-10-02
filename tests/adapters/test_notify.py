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


if __name__ == "__main__":
    unittest.main()
