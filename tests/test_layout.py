"""Guards for the repository layout and the package layering rule.

The factory is an agent configuration, so WHERE its pieces live is load-bearing:
opencode resolves agents through `.opencode/agents`, the evals read `agents/` and
`evals/cases/`, the tester prompt embeds `agents/policies/REVIEW.md`, and the trust
package validates against a schema shipped inside the package. A move that breaks
one of those paths fails silently at run time, so it is pinned here instead.

Layering (arrows point one way only)::

    interfaces -> runs -> {pipeline, verification, evidence, workspace,
                           selftest, agent_config} -> domain

adapters and state may be used by the middle layers; domain imports nothing from
factory; NOTHING imports interfaces except the console-script entry point.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "src" / "factory"
AGENTS = ("spec-agent", "architect-agent", "coder-agent", "tester-agent")

MIDDLE = {"pipeline", "verification", "evidence", "workspace", "selftest", "agent_config"}
INFRA = {"adapters", "state"}

# Edges that break the rule today, each with the reason it is tolerated. This list
# may only SHRINK: a new violation fails the suite, and fixing a listed one fails it
# too until the entry is removed (so the list cannot rot into a blanket waiver).
KNOWN_VIOLATIONS: dict[tuple[str, str], str] = {
    ("factory.selftest.simulate", "factory.interfaces.board.data"): (
        "simulate renders each scenario's stage flow with board.data's "
        "run_pipeline_progress/render_flow; that read model needs a home below "
        "interfaces (not runs: selftest may not import runs either)."
    ),
}


def _module_name(path: Path) -> str:
    rel = path.relative_to(PACKAGE.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _factory_imports(path: Path) -> set[str]:
    """Every factory module a file imports, including function-level imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith("factory"))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "factory":
                found.update(f"factory.{a.name}" for a in node.names)
            elif node.module.startswith("factory."):
                found.add(node.module)
    return found


def _layer(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ""


def _violation(src: str, dst: str) -> str | None:
    s, d = _layer(src), _layer(dst)
    if s == d or d == "":
        return None
    if d == "interfaces":
        return "nothing outside interfaces may import interfaces"
    if s == "domain":
        return "domain imports nothing from factory"
    if s in INFRA and d != "domain" and d not in INFRA:
        return "adapters/state may depend only on domain"
    if s in MIDDLE and d == "runs":
        return "middle layers may not import runs (runs sits above them)"
    return None


class LayeringTests(unittest.TestCase):
    def _edges(self) -> list[tuple[str, str]]:
        edges = []
        for path in sorted(PACKAGE.rglob("*.py")):
            src = _module_name(path)
            edges += [(src, dst) for dst in sorted(_factory_imports(path))]
        return edges

    def test_imports_respect_the_layering_rule(self) -> None:
        broken = [
            f"{src} -> {dst}: {why}"
            for src, dst in self._edges()
            if (why := _violation(src, dst)) and (src, dst) not in KNOWN_VIOLATIONS
        ]
        self.assertEqual(broken, [], "layering violations:\n" + "\n".join(broken))

    def test_known_violations_are_still_real(self) -> None:
        """A fixed violation must be removed from the allowlist, not left to rot."""
        edges = set(self._edges())
        stale = [f"{s} -> {d}" for s, d in KNOWN_VIOLATIONS if (s, d) not in edges]
        self.assertEqual(stale, [], "remove fixed entries from KNOWN_VIOLATIONS")

    def test_the_rule_itself_flags_what_it_should(self) -> None:
        self.assertIsNotNone(_violation("factory.domain.gates", "factory.state.db"))
        self.assertIsNotNone(_violation("factory.runs.service", "factory.interfaces.cli"))
        self.assertIsNotNone(_violation("factory.selftest.evals", "factory.interfaces.render"))
        self.assertIsNotNone(_violation("factory.pipeline.graph", "factory.runs.service"))
        self.assertIsNotNone(_violation("factory.state.db", "factory.workspace.git"))
        self.assertIsNone(_violation("factory.pipeline.graph", "factory.domain.gates"))
        self.assertIsNone(_violation("factory.interfaces.cli", "factory.runs.service"))
        self.assertIsNone(_violation("factory.evidence.trust_package", "factory.state.db"))


class AgentConfigLayoutTests(unittest.TestCase):
    def test_opencode_agents_is_a_relative_symlink_to_agents(self) -> None:
        link = REPO_ROOT / ".opencode" / "agents"
        self.assertTrue(link.is_symlink(), ".opencode/agents must be a symlink")
        # Relative, so it survives a clone to any path (an absolute link would
        # point at the author's machine).
        self.assertFalse(Path(link.readlink()).is_absolute())
        self.assertEqual(link.resolve(), (REPO_ROOT / "agents").resolve())

    def test_every_agent_resolves_where_opencode_looks(self) -> None:
        for agent in AGENTS:
            with self.subTest(agent=agent):
                self.assertTrue((REPO_ROOT / ".opencode" / "agents" / f"{agent}.md").is_file())

    def test_project_repos_link_to_the_repo_root_opencode_dir(self) -> None:
        from factory.workspace import projects

        self.assertEqual(projects._package_root(), REPO_ROOT)
        self.assertTrue((projects._package_root() / ".opencode" / "agents" / "coder-agent.md").is_file())

    def test_default_paths_point_at_real_files(self) -> None:
        from factory.agent_config import review_policy
        from factory.evidence import trust_package
        from factory.selftest import evals

        self.assertEqual(evals.default_agents_dir(), REPO_ROOT / "agents")
        self.assertEqual(evals.default_cases_dir(), REPO_ROOT / "evals" / "cases")
        self.assertTrue(any(evals.default_cases_dir().glob("*.json")))
        self.assertEqual(review_policy.default_policy_path(), REPO_ROOT / "agents" / "policies" / "REVIEW.md")
        self.assertTrue(review_policy.default_policy_path().is_file())
        # Package data, not a repo doc: it must sit inside the package to ship in the wheel.
        self.assertTrue(trust_package._SCHEMA_PATH.is_file())
        self.assertTrue(trust_package._SCHEMA_PATH.is_relative_to(PACKAGE))


if __name__ == "__main__":
    unittest.main()
