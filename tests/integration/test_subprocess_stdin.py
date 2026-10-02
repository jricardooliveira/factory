"""No factory subprocess may inherit the orchestrator's stdin.

Found by a live validation run: `opencode run` READS STDIN when it is not a
terminal, and waits for EOF before it even starts. Run in the foreground from a
terminal it worked; run in the background (or under cron, CI, a scheduler, or the
board's TUI worker) it inherited a stdin pipe that never closes and hung until the
600-second agent timeout — every coder task, every time. Reproduced directly:
stdin inherited → 60s with zero output; stdin=/dev/null → answered in 7s.

The orchestrator never has anything to say to a child on stdin, so every
subprocess it starts gets DEVNULL. That also covers `go test` / `pytest` on
agent-written code, which could otherwise block on a stray `fmt.Scanln`.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _done(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


class AgentCallStdinTests(unittest.TestCase):
    def test_run_agent_never_inherits_stdin(self) -> None:
        from factory.adapters.opencode import run_agent

        with patch("factory.adapters.opencode.subprocess.run", return_value=_done()) as run:
            run_agent("coder-agent", "do the thing", cwd=".", model="openai/gpt-5.5-fast")
        self.assertIs(
            run.call_args.kwargs.get("stdin"), subprocess.DEVNULL,
            "opencode blocks reading an inherited stdin; it must get DEVNULL",
        )


class VerifyStdinTests(unittest.TestCase):
    def test_verify_commands_never_inherit_stdin(self) -> None:
        from factory.verification import base

        with patch("factory.verification.base.subprocess.run", return_value=_done()) as run:
            base.run_command(["go", "build", "./..."], Path("."))
        self.assertIs(run.call_args.kwargs.get("stdin"), subprocess.DEVNULL)

    def test_running_agent_written_tests_never_inherits_stdin(self) -> None:
        """Agent-written test code reading stdin must not hang the gate: every
        container-runtime call the sandbox makes passes stdin=DEVNULL."""
        from factory.verification import python as verify_python
        from factory.verification import sandbox

        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict("os.environ", {"FACTORY_RUN_TESTS": "1"}), \
                patch.object(sandbox, "container_runtime", return_value="docker"), \
                patch.object(sandbox, "_ensure_image", return_value=None), \
                patch("factory.verification.sandbox.subprocess.run", return_value=_done()) as run:
            verify_python.run_tests(Path(tmp))
        self.assertTrue(run.call_args_list)
        for call in run.call_args_list:
            self.assertIs(call.kwargs.get("stdin"), subprocess.DEVNULL)


class ArtifactAuthorStdinTests(unittest.TestCase):
    def test_git_author_lookup_never_inherits_stdin(self) -> None:
        from factory.evidence import artifacts

        with patch("factory.evidence.artifacts.subprocess.run", return_value=_done("Jo\n")) as run, \
                patch("factory.evidence.artifacts.shutil.which", return_value="/usr/bin/git"):
            artifacts._git_author()
        self.assertIs(run.call_args.kwargs.get("stdin"), subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
