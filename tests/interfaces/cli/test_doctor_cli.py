"""`factory doctor` CLI: the exit code is the contract scripts and CI rely on."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from factory.interfaces.cli import main as cli_main
from factory.interfaces.cli import selftest
from factory.preflight.doctor import Check, DoctorReport


def _report(*checks: Check) -> DoctorReport:
    return DoctorReport(checks=list(checks))


class DoctorCommandTests(unittest.TestCase):
    def test_doctor_is_a_registered_verb(self) -> None:
        self.assertIs(cli_main.COMMANDS["doctor"], selftest.doctor_command)

    def test_exits_zero_when_every_blocking_check_passes(self) -> None:
        report = _report(Check("opencode", "ok", "/usr/bin/opencode", blocking=True))
        with patch("factory.preflight.doctor.run_doctor", return_value=report) as run:
            selftest.doctor_command([])
        run.assert_called_once_with(offline=False)

    def test_exits_non_zero_when_a_model_is_unreachable(self) -> None:
        report = _report(Check("model openai/x", "fail", "ERROR: nope", blocking=True))
        with patch("factory.preflight.doctor.run_doctor", return_value=report):
            with self.assertRaises(SystemExit) as exc:
                selftest.doctor_command([])
        self.assertNotEqual(exc.exception.code, 0)

    def test_offline_flag_is_passed_through(self) -> None:
        report = _report(Check("model openai/x", "skip", "--offline", blocking=True))
        with patch("factory.preflight.doctor.run_doctor", return_value=report) as run:
            selftest.doctor_command(["--offline"])
        run.assert_called_once_with(offline=True)


if __name__ == "__main__":
    unittest.main()
