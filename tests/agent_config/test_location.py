"""The agent configuration must be findable — or its absence survivable — outside
a source checkout.

Found by review: `agent_config.tiers` loaded ``<checkout>/agents/tiers.toml`` at
IMPORT time, and every CLI verb imports it through the pipeline. A built wheel
(no checkout around it) therefore crashed on `factory list` and `factory --help`
before dispatch, and `factory doctor` — the command meant to diagnose a broken
config — could not even start.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from factory.agent_config import location

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "src" / "factory"


def _detached_copy(dest: Path, *, bundle_agents: bool) -> Path:
    """The factory package alone, nested three levels deep so `parents[3]` is a
    directory with no agents/ in it — what a site-packages install looks like."""
    site = dest / "a" / "b" / "c"
    shutil.copytree(PACKAGE, site / "factory", ignore=shutil.ignore_patterns("__pycache__"))
    if bundle_agents:
        shutil.copytree(REPO_ROOT / "agents", site / "factory" / "_agents")
    return site


def _run(site: Path, code: str, home: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(site), "FACTORY_HOME": str(home)}
    env.pop(location.AGENTS_DIR_ENV, None)
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env,
        cwd=str(site), timeout=120, stdin=subprocess.DEVNULL,
    )


class DetachedInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_read_only_verbs_work_without_any_agent_config(self) -> None:
        site = _detached_copy(self.tmp, bundle_agents=False)
        proc = _run(
            site,
            "import sys; sys.argv = ['factory', 'list']\n"
            "from factory.interfaces.cli.main import main\nmain()",
            self.home,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("No runs found", proc.stdout)

    def test_a_wheel_style_install_resolves_tiers_from_the_bundled_copy(self) -> None:
        site = _detached_copy(self.tmp, bundle_agents=True)
        proc = _run(
            site,
            "from factory.agent_config import tiers, review_policy\n"
            "print(tiers.resolve_model('coder-agent')[1])\n"
            "print(bool(review_policy.load_review_policy()))",
            self.home,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split(), ["fast", "True"])


class AgentsDirTests(unittest.TestCase):
    def test_a_checkout_uses_its_own_agents_directory(self) -> None:
        self.assertEqual(location.agents_dir(), REPO_ROOT / "agents")

    def test_the_env_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ[location.AGENTS_DIR_ENV] = tmp
            try:
                self.assertEqual(location.agents_dir(), Path(tmp).resolve())
            finally:
                del os.environ[location.AGENTS_DIR_ENV]

    def test_the_wheel_bundles_the_agent_configuration(self) -> None:
        import tomllib

        build = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]
        force = build["targets"]["wheel"]["force-include"]
        self.assertEqual(force.get("agents"), "factory/_agents")


if __name__ == "__main__":
    unittest.main()
