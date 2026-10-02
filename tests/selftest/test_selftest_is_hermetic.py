"""The eval corpus and the scenario matrix never start a container.

They replay FROZEN agent outputs to test orchestration; their verdict must not
depend on whether Docker is running or PyPI is reachable. With a runtime up, the
default (FACTORY_RUN_TESTS unset = auto) would run each frozen run's generated tests
in a container — slow, networked, and judged against code nobody re-wrote. Test
execution has its own proofs (tests/verification/test_sandbox*.py).
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from factory.selftest import simulate
from factory.selftest.evals import cases
from factory.verification import sandbox


class SelftestIsHermeticTests(unittest.TestCase):
    def _no_container(self):
        env = {k: v for k, v in os.environ.items() if k != "FACTORY_RUN_TESTS"}  # = auto
        return (patch.dict(os.environ, env, clear=True),
                patch.object(sandbox, "container_runtime", return_value="docker"),
                patch.object(sandbox, "run_in_container",
                             side_effect=AssertionError("a self-test started a container")))

    def test_the_eval_corpus_runs_no_container(self) -> None:
        env, runtime, container = self._no_container()
        case = next(c for c in cases.load_cases() if c.name == "real-run-16-completed")
        with env, runtime, container:
            result = cases.run_case(case)
        self.assertTrue(result.passed, result.detail)
        self.assertNotIn("FACTORY_RUN_TESTS", os.environ.get("_unused", ""))

    def test_the_scenario_matrix_runs_no_container(self) -> None:
        env, runtime, container = self._no_container()
        scenario = next(s for s in simulate.CATALOG if s.name == "greenfield_clean")
        with env, runtime, container:
            result = simulate.run_scenario(scenario)
        self.assertTrue(result.passed, result.actual_status)

    def test_the_operators_setting_is_restored_afterwards(self) -> None:
        env, runtime, container = self._no_container()
        scenario = next(s for s in simulate.CATALOG if s.name == "greenfield_clean")
        with env, runtime, container:
            os.environ["FACTORY_RUN_TESTS"] = "1"
            simulate.run_scenario(scenario)
            self.assertEqual(os.environ["FACTORY_RUN_TESTS"], "1")


if __name__ == "__main__":
    unittest.main()
