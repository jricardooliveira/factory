"""The pipeline package's shape: a short public API, and one-way imports inside it.

`factory.pipeline` was a single 1,300-line module. It is now a package whose
`__init__` only re-exports; other packages import from there and never reach into
the submodules, so the internals can keep moving without breaking the CLI, the
board or the self-tests. Inside the package the imports point one way:

    graph -> nodes -> {prompts, agent_calls} -> state

(prompts never call an agent; agent_calls never builds a prompt; nodes never
wire the graph). Tests may import internals — they patch where names are looked up.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import factory.pipeline as pipeline

PACKAGE = Path(pipeline.__file__).resolve().parent
SRC = PACKAGE.parents[1]

# For each first-level part of factory.pipeline, the parts it may import.
ALLOWED: dict[str, set[str]] = {
    "state": set(),
    "agent_calls": {"state"},
    "prompts": {"state", "prompts"},
    "nodes": {"state", "agent_calls", "prompts", "nodes"},
    "graph": {"state", "nodes"},
}


def _imports(path: Path) -> list[tuple[str, list[str]]]:
    """(module, imported names) for every absolute factory import in a file."""
    found: list[tuple[str, list[str]]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found += [(a.name, []) for a in node.names if a.name.startswith("factory")]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "factory":
                found += [(f"factory.{a.name}", []) for a in node.names]
            elif node.module.startswith("factory."):
                found.append((node.module, [a.name for a in node.names]))
    return found


def _part(module: str) -> str | None:
    """'factory.pipeline.nodes.coder' -> 'nodes'; None outside the package."""
    bits = module.split(".")
    if bits[:2] != ["factory", "pipeline"]:
        return None
    return bits[2] if len(bits) > 2 else ""


def _outside_violations(path: Path) -> list[str]:
    problems = []
    for module, names in _imports(path):
        part = _part(module)
        if part is None:
            continue
        if part:
            problems.append(f"imports internal module {module}")
        else:
            problems += [f"imports non-public name {n}" for n in names if n not in pipeline.__all__]
    return problems


def _inside_violations(path: Path) -> list[str]:
    rel = path.relative_to(PACKAGE).with_suffix("")
    me = rel.parts[0] if rel.parts[0] != "__init__" else ""
    if me == "":
        return []  # the public API re-exports from anywhere in the package
    return [
        f"{me} may not import {module}"
        for module, _names in _imports(path)
        if (part := _part(module)) is not None and part not in ALLOWED[me]
    ]


class PublicApiTests(unittest.TestCase):
    def test_every_exported_name_resolves(self) -> None:
        for name in pipeline.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(pipeline, name))

    def test_init_only_re_exports(self) -> None:
        tree = ast.parse((PACKAGE / "__init__.py").read_text(encoding="utf-8"))
        defined = [
            n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        self.assertEqual(defined, [], "pipeline/__init__.py must stay a re-export list")

    def test_other_packages_use_only_the_public_api(self) -> None:
        problems = [
            f"{path.relative_to(SRC)}: {why}"
            for path in sorted((SRC / "factory").rglob("*.py"))
            if not path.is_relative_to(PACKAGE)
            for why in _outside_violations(path)
        ]
        self.assertEqual(problems, [], "\n".join(problems))

    def test_imports_inside_the_package_point_one_way(self) -> None:
        self.assertEqual(set(ALLOWED), {
            p.stem if p.is_file() else p.name
            for p in PACKAGE.iterdir()
            if (p.suffix == ".py" and p.stem != "__init__") or (p / "__init__.py").is_file()
        }, "a new pipeline module needs an entry in ALLOWED")
        problems = [
            f"{path.relative_to(PACKAGE)}: {why}"
            for path in sorted(PACKAGE.rglob("*.py"))
            for why in _inside_violations(path)
        ]
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_rules_flag_what_they_should(self) -> None:
        self.assertEqual(_part("factory.pipeline"), "")
        self.assertEqual(_part("factory.pipeline.nodes.coder"), "nodes")
        self.assertIsNone(_part("factory.runs"))
        self.assertNotIn("graph", ALLOWED["nodes"])
        self.assertNotIn("agent_calls", ALLOWED["prompts"])
        self.assertNotIn("nodes", ALLOWED["prompts"])


if __name__ == "__main__":
    unittest.main()
