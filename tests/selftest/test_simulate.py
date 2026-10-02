"""The offline scenario matrix must keep behaving as documented."""

from __future__ import annotations

import unittest

from factory.selftest.simulate import CATALOG, render_markdown, run_scenario, simulate_all


class SimulationTests(unittest.TestCase):
    def test_every_scenario_matches_expected_outcome(self) -> None:
        results = simulate_all()
        self.assertEqual(len(results), len(CATALOG))
        failures = [
            f"{r.scenario.name}: expected {r.scenario.expected_status}, "
            f"got {r.actual_status} ({r.error})"
            for r in results
            if not r.passed
        ]
        self.assertEqual(failures, [], "scenarios drifted: " + "; ".join(failures))

    def test_governance_scenario_blocks(self) -> None:
        gov = next(s for s in CATALOG if s.name == "governance_violation")
        self.assertEqual(run_scenario(gov).actual_status, "blocked")

    def test_report_renders_with_counts(self) -> None:
        md = render_markdown(simulate_all())
        self.assertIn("Factory Simulation Report", md)
        self.assertIn("scenarios behaving as expected", md)
        self.assertIn("greenfield_clean", md)


if __name__ == "__main__":
    unittest.main()
