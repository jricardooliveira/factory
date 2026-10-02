"""Guards for the repository layout and the package layering rule.

The factory is an agent configuration, so WHERE its pieces live is load-bearing:
opencode resolves agents through `.opencode/agents`, the evals read `agents/` and
`evals/cases/`, the tester prompt embeds `agents/policies/REVIEW.md`, and the trust
package validates against a schema shipped inside the package. A move that breaks
one of those paths fails silently at run time, so it is pinned here instead.

Layering (arrows point one way only)::

    interfaces -> {runs, selftest, preflight}
    selftest   -> runs -> pipeline -> evidence -> verification -> workspace
                                                       -> agent_config -> domain
    adapters and state serve the middle layers and depend only on domain.

`ALLOWED` spells the rule out per top-level package: an import edge not listed
there fails the suite. Three more guards back it up: no module-level import
cycle anywhere (Tarjan SCCs), no SQL outside `factory.state`, and nothing but
the console-script entry point reaching into `interfaces`. Relative imports and
`importlib.import_module("factory...")` calls count as edges too.
"""

from __future__ import annotations

import ast
import unittest
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "src" / "factory"
AGENTS = ("spec-agent", "architect-agent", "boundary-agent", "coder-agent", "tester-agent",
          "release-agent", "interview-agent", "backlog-agent")

# Which top-level packages each package may import (besides itself). The table is
# the rule; it is acyclic by construction (each row names only rows below it).
ALLOWED: dict[str, frozenset[str]] = {
    "interfaces": frozenset({"runs", "selftest", "preflight", "evidence", "workspace",
                             "agent_config", "domain"}),
    "selftest": frozenset({"runs", "pipeline", "evidence", "verification", "workspace",
                           "agent_config", "state", "domain"}),
    "preflight": frozenset({"agent_config", "workspace", "adapters", "state", "domain"}),
    "runs": frozenset({"pipeline", "evidence", "verification", "workspace", "agent_config",
                       "adapters", "state", "domain"}),
    "pipeline": frozenset({"evidence", "verification", "workspace", "agent_config",
                           "adapters", "state", "domain"}),
    "evidence": frozenset({"verification", "workspace", "agent_config", "state", "domain"}),
    "verification": frozenset({"workspace", "domain"}),
    "workspace": frozenset({"agent_config", "state", "domain"}),
    "agent_config": frozenset({"domain"}),
    "adapters": frozenset({"domain"}),
    "state": frozenset({"domain"}),
    "domain": frozenset(),
}

# Edges that break the rule today, each with the reason it is tolerated. This list
# may only SHRINK: a new violation fails the suite, and fixing a listed one fails it
# too until the entry is removed (so the list cannot rot into a blanket waiver).
KNOWN_VIOLATIONS: dict[tuple[str, str], str] = {}

# Module-level import cycles tolerated today (same shrink-only contract).
KNOWN_CYCLES: list[frozenset[str]] = []

# Modules allowed to talk SQL. Everything else goes through their accessors.
SQL_OWNERS = ("factory.state",)


def _module_name(path: Path) -> str:
    rel = path.relative_to(PACKAGE.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_package(module: str) -> bool:
    return (PACKAGE.parent / module.replace(".", "/") / "__init__.py").is_file()


def _exists(module: str) -> bool:
    base = PACKAGE.parent / module.replace(".", "/")
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _resolve_relative(src: str, is_pkg: bool, level: int, module: str | None) -> str:
    parts = src.split(".") if is_pkg else src.split(".")[:-1]
    if level > 1:
        parts = parts[: len(parts) - (level - 1)]
    return ".".join(parts + ([module] if module else []))


def _type_checking_block(node: ast.AST) -> bool:
    test = getattr(node, "test", None)
    return isinstance(node, ast.If) and (
        (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
        or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
    )


def _walk(tree: ast.AST, *, top_level_only: bool) -> Iterator[ast.AST]:
    """Every node, or (top_level_only) only those executed at import time: not
    inside a function body, not under `if TYPE_CHECKING:`."""
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if top_level_only and (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                or _type_checking_block(child)
            ):
                continue
            stack.append(child)


def _imports(path: Path, *, top_level_only: bool = False) -> set[str]:
    return _imports_of(
        path.read_text(encoding="utf-8"), _module_name(path), path.name == "__init__.py",
        top_level_only=top_level_only,
    )


def _imports_of(text: str, src: str, is_pkg: bool, *, top_level_only: bool = False) -> set[str]:
    """Every factory module a source imports — absolute, relative and via
    `importlib.import_module("factory...")` — resolved to the most specific module
    (`from factory.x import y` is an edge to `factory.x.y` when y is a module)."""
    tree = ast.parse(text)
    found: set[str] = set()
    for node in _walk(tree, top_level_only=top_level_only):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith("factory"))
        elif isinstance(node, ast.ImportFrom):
            base = (
                _resolve_relative(src, is_pkg, node.level, node.module)
                if node.level
                else (node.module or "")
            )
            if base != "factory" and not base.startswith("factory."):
                continue
            for alias in node.names:
                sub = f"{base}.{alias.name}"
                found.add(sub if _exists(sub) else base)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))
            and getattr(node.func, "attr", getattr(node.func, "id", "")) == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and str(node.args[0].value).startswith("factory")
        ):
            found.add(str(node.args[0].value))
    found.discard(src)
    return found


def _layer(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ""


def _violation(src: str, dst: str) -> str | None:
    s, d = _layer(src), _layer(dst)
    if s == d or d == "" or s == "":
        return None
    if d == "interfaces":
        return "nothing outside interfaces may import interfaces"
    if s not in ALLOWED:
        return f"package {s!r} has no row in ALLOWED"
    if d not in ALLOWED[s]:
        return f"{s} may import only {sorted(ALLOWED[s])}"
    return None


def _edges(*, top_level_only: bool = False) -> list[tuple[str, str]]:
    edges = []
    for path in sorted(PACKAGE.rglob("*.py")):
        src = _module_name(path)
        edges += [(src, dst) for dst in sorted(_imports(path, top_level_only=top_level_only))]
    return edges


def _cycles(edges: list[tuple[str, str]]) -> list[frozenset[str]]:
    """Strongly-connected components with more than one module (Tarjan)."""
    graph: dict[str, list[str]] = {}
    for a, b in edges:
        graph.setdefault(a, []).append(b)
        graph.setdefault(b, [])
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    found: list[frozenset[str]] = []
    counter = 0

    def visit(v: str) -> None:
        nonlocal counter
        index[v] = low[v] = counter
        counter += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            component = set()
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.add(w)
                if w == v:
                    break
            if len(component) > 1:
                found.append(frozenset(component))

    for v in sorted(graph):
        if v not in index:
            visit(v)
    return found


def _sql_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("execute", "executemany", "executescript")
    ]


class LayeringTests(unittest.TestCase):
    def test_imports_respect_the_layering_rule(self) -> None:
        broken = [
            f"{src} -> {dst}: {why}"
            for src, dst in _edges()
            if (why := _violation(src, dst)) and (src, dst) not in KNOWN_VIOLATIONS
        ]
        self.assertEqual(broken, [], "layering violations:\n" + "\n".join(broken))

    def test_known_violations_are_still_real(self) -> None:
        """A fixed violation must be removed from the allowlist, not left to rot."""
        edges = set(_edges())
        stale = [f"{s} -> {d}" for s, d in KNOWN_VIOLATIONS if (s, d) not in edges]
        self.assertEqual(stale, [], "remove fixed entries from KNOWN_VIOLATIONS")

    def test_every_top_level_package_has_a_row(self) -> None:
        packages = {p.name for p in PACKAGE.iterdir() if (p / "__init__.py").is_file()}
        self.assertEqual(packages, set(ALLOWED), "a new package needs a row in ALLOWED")

    def test_the_allowed_table_is_itself_acyclic(self) -> None:
        edges = [(a, b) for a, targets in ALLOWED.items() for b in targets]
        self.assertEqual(_cycles(edges), [])

    def test_no_module_level_import_cycles(self) -> None:
        """Whether a cycle imports cleanly depends on statement order — exactly
        how `verification/__init__` came to import its toolchains mid-file."""
        cycles = [c for c in _cycles(_edges(top_level_only=True)) if c not in KNOWN_CYCLES]
        self.assertEqual(cycles, [], "module-level import cycles")

    def test_no_cycles_even_through_function_level_imports(self) -> None:
        """A function-level import that exists to dodge a cycle is the same cycle
        (`workspace.projects` <-> `templates` was one)."""
        cycles = [c for c in _cycles(_edges()) if c not in KNOWN_CYCLES]
        self.assertEqual(cycles, [], "import cycles (including lazy imports)")

    def test_known_cycles_are_still_real(self) -> None:
        current = _cycles(_edges(top_level_only=True))
        self.assertEqual([c for c in KNOWN_CYCLES if c not in current], [])

    def test_sql_lives_only_in_state(self) -> None:
        """`factory.state` owns every SQL statement; everyone else calls an accessor."""
        offenders = [
            f"{_module_name(path)}:{line}"
            for path in sorted(PACKAGE.rglob("*.py"))
            if not _module_name(path).startswith(SQL_OWNERS)
            for line in _sql_calls(path)
        ]
        self.assertEqual(offenders, [])

    def test_render_is_presentation_only(self) -> None:
        """interfaces/render/ takes data and prints it; the command modules fetch.
        A render helper that opened the DB or drove a run would put orchestration
        back into the presentation layer that `runs/` was extracted from."""
        allowed = ("factory.domain", "factory.runs", "factory.evidence.progress",
                   "factory.interfaces.render")
        reaches = sorted(
            m for path in (PACKAGE / "interfaces" / "render").rglob("*.py")
            for m in _imports(path)
            if not m.startswith(allowed)
        )
        self.assertEqual(reaches, [])

    def test_console_script_resolves_to_the_cli_main_module(self) -> None:
        import importlib
        import tomllib

        target = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["scripts"][
            "factory"
        ]
        self.assertEqual(target, "factory.interfaces.cli.main:main")
        module, _, attr = target.partition(":")
        self.assertTrue(callable(getattr(importlib.import_module(module), attr)))


class GuardSelfTests(unittest.TestCase):
    """The guard must flag what it exists to flag — each case below passed the
    previous, looser rule."""

    def test_the_rule_itself_flags_what_it_should(self) -> None:
        flagged = [
            ("factory.domain.gates", "factory.state.db"),
            ("factory.runs.service", "factory.interfaces.cli"),
            ("factory.selftest.evals", "factory.interfaces.render"),
            ("factory.pipeline.graph", "factory.runs.service"),
            ("factory.state.db", "factory.workspace.git"),
            # interfaces skipping runs to reach pipeline internals or the DB
            ("factory.interfaces.cli.run", "factory.pipeline.nodes.coder"),
            ("factory.interfaces.board.tui", "factory.state.db"),
            # middle packages importing each other "upwards"
            ("factory.pipeline.graph", "factory.selftest.evals"),
            ("factory.evidence.trust_package", "factory.pipeline.nodes.coder"),
            ("factory.runs.service", "factory.selftest.simulate"),
            ("factory.workspace.projects", "factory.evidence.adr"),
        ]
        for src, dst in flagged:
            with self.subTest(edge=f"{src} -> {dst}"):
                self.assertIsNotNone(_violation(src, dst))
        allowed = [
            ("factory.pipeline.graph", "factory.domain.gates"),
            ("factory.interfaces.cli", "factory.runs.service"),
            ("factory.evidence.trust_package", "factory.state.db"),
            ("factory.selftest.simulate", "factory.runs.service"),
        ]
        for src, dst in allowed:
            with self.subTest(edge=f"{src} -> {dst}"):
                self.assertIsNone(_violation(src, dst))

    def test_relative_imports_are_resolved(self) -> None:
        body = "from ..interfaces import render\ndef f():\n    from . import graph\n"
        found = _imports_of(body, "factory.pipeline.probe", False)
        self.assertEqual(found, {"factory.interfaces.render", "factory.pipeline.graph"})
        # The function-level import is not a module-level (cycle-forming) edge.
        top = _imports_of(body, "factory.pipeline.probe", False, top_level_only=True)
        self.assertEqual(top, {"factory.interfaces.render"})
        self.assertIsNotNone(_violation("factory.pipeline.probe", "factory.interfaces.render"))

    def test_importlib_imports_are_seen(self) -> None:
        body = "import importlib\nimportlib.import_module('factory.interfaces.render')\n"
        self.assertEqual(
            _imports_of(body, "factory.runs.probe", False), {"factory.interfaces.render"}
        )

    def test_type_checking_imports_are_not_cycle_edges(self) -> None:
        body = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from factory.runs import service\n"
        self.assertEqual(_imports_of(body, "factory.pipeline.probe", False, top_level_only=True), set())

    def test_cycle_detection_finds_a_cycle(self) -> None:
        self.assertEqual(
            _cycles([("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")]),
            [frozenset({"a", "b", "c"})],
        )

    def test_sql_detection_finds_a_raw_query(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write("def f(conn):\n    return conn.execute('SELECT 1')\n")
        try:
            self.assertEqual(_sql_calls(Path(fh.name)), [2])
        finally:
            Path(fh.name).unlink()


class AgentConfigLayoutTests(unittest.TestCase):
    def test_opencode_agents_holds_one_relative_link_per_agent(self) -> None:
        """NOT a link to the whole agents/ directory: opencode scans it recursively
        with symlinks followed, so agents/policies/REVIEW.md became a fifth agent
        (with write tools enabled). One link per agent exposes exactly the four."""
        folder = REPO_ROOT / ".opencode" / "agents"
        self.assertTrue(folder.is_dir() and not folder.is_symlink())
        self.assertEqual(sorted(p.name for p in folder.iterdir()), sorted(f"{a}.md" for a in AGENTS))
        for agent in AGENTS:
            with self.subTest(agent=agent):
                link = folder / f"{agent}.md"
                self.assertTrue(link.is_symlink())
                # Relative, so it survives a clone to any path (an absolute link
                # would point at the author's machine).
                self.assertFalse(Path(link.readlink()).is_absolute())
                self.assertEqual(link.resolve(), (REPO_ROOT / "agents" / f"{agent}.md").resolve())

    def test_every_agent_resolves_where_opencode_looks(self) -> None:
        for agent in AGENTS:
            with self.subTest(agent=agent):
                self.assertTrue((REPO_ROOT / ".opencode" / "agents" / f"{agent}.md").is_file())

    def test_project_repos_link_to_the_repo_root_opencode_dir(self) -> None:
        from factory.workspace import projects

        self.assertEqual(projects.checkout_root(), REPO_ROOT)
        self.assertTrue((projects.checkout_root() / ".opencode" / "agents" / "coder-agent.md").is_file())

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
