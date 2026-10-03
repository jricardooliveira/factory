"""Python checks: py_compile and a static import check by default; the test suite only
on opt-in (FACTORY_RUN_TESTS), because running it executes agent-written code."""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

from factory.verification.base import suite_timeout, VerifyCheck, run_command, tests_enabled


def product_python(root: Path) -> str:
    """The interpreter that runs the PRODUCT's tests: its own venv, else
    $FACTORY_PRODUCT_PYTHON, else the factory's. The factory's venv lacks the
    product's dependencies, so running tests with it fails for the wrong reason."""
    for venv_python in (root / ".venv" / "bin" / "python", root / ".venv" / "Scripts" / "python.exe"):
        if venv_python.is_file():
            return str(venv_python.absolute())  # never resolve(): the venv python is a symlink
    return os.environ.get("FACTORY_PRODUCT_PYTHON", "").strip() or sys.executable


def _has_pytest(python: str) -> bool:
    # Only the factory's own interpreter can be asked cheaply; a product venv
    # without pytest fails loudly when run, which is the honest verdict.
    return python != sys.executable or importlib.util.find_spec("pytest") is not None


def py_compile_check(py_files: list[Path], root: Path) -> VerifyCheck:
    try:
        proc = run_command([sys.executable, "-m", "py_compile", *[str(p) for p in py_files]], root)
    except subprocess.TimeoutExpired:
        return VerifyCheck("py_compile", "warn", "timed out")
    if proc.returncode != 0:
        return VerifyCheck("py_compile", "fail", (proc.stderr or proc.stdout).strip()[:500])
    return VerifyCheck("py_compile", "pass", f"{len(py_files)} file(s)")


def _is_local_module(top: str, root: Path) -> bool:
    """True if `top` is a module that lives in the repo (so a missing import of it
    means the coder's own code is broken, not that a third-party dep is absent)."""
    if (root / f"{top}.py").is_file():
        return True
    if (root / top).is_dir():
        return True
    # Common src-layout: src/<top>/ or src/<top>.py
    src = root / "src"
    return (src / top).is_dir() or (src / f"{top}.py").is_file()


def _module_target(root: Path, parts: list[str]) -> Path | None:
    """The file (or package dir) a dotted local module resolves to, else None."""
    for base in (root, root / "src"):
        target = base.joinpath(*parts)
        if target.with_suffix(".py").is_file():
            return target.with_suffix(".py")
        if target.is_dir():
            init = target / "__init__.py"
            return init if init.is_file() else target  # a namespace package is a dir
    return None


def _top_level_names(module: Path) -> set[str] | None:
    """Names a module defines at top level; None when that cannot be known statically
    (a star import, a module `__getattr__`, or an unparsable file)."""
    try:
        tree = ast.parse(module.read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return None
    names: set[str] = set()
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    return None
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        # Conditional definitions (if / try / with) still define names at top level.
        for field in ("body", "orelse", "finalbody", "handlers"):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                pending.extend(getattr(node, field, None) or [])
    return None if "__getattr__" in names else names


def _relative_parts(path: Path, root: Path, level: int, module: str | None) -> list[str] | None:
    package = path.parent
    for _ in range(level - 1):
        package = package.parent
    try:
        rel = package.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    parts = [p for p in rel.parts if p != "src"] if rel.parts[:1] == ("src",) else list(rel.parts)
    return parts + (module.split(".") if module else [])


def static_import_check(py_files: list[Path], root: Path) -> VerifyCheck:
    """Do the changed files import local modules — and names — that exist? Runs nothing.

    What `pytest --collect-only` caught by IMPORTING the code (a coder referencing
    a module or symbol it never wrote), found by parsing it instead. Third-party
    imports are not judged: whether a dependency is installed is not the code's
    fault. Syntax errors are py_compile's to report.
    """
    problems: list[str] = []
    for path in py_files:
        path = path if path.is_absolute() else root / path
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        where = path.relative_to(root) if path.is_relative_to(root) else path
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [(a.name.split("."), []) for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level:
                parts = _relative_parts(path, root, node.level, node.module)
                if parts is None or not node.module:
                    continue  # `from . import x`: x may be a submodule or a name
                imports = [(parts, [a.name for a in node.names])]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports = [(node.module.split("."), [a.name for a in node.names])]
            else:
                continue
            for parts, wanted in imports:
                relative = isinstance(node, ast.ImportFrom) and node.level > 0
                if not relative and not _is_local_module(parts[0], root):
                    continue  # not this repo's code
                target = _module_target(root, parts)
                dotted = ".".join(parts)
                if target is None:
                    problems.append(f"{where}: imports local module '{dotted}', which does not exist")
                    continue
                if not wanted or "*" in wanted:
                    continue
                defined = _top_level_names(target) if target.suffix == ".py" else set()
                for name in wanted:
                    submodule = _module_target(root, [*parts, name])
                    if submodule is None and defined is not None and name not in defined:
                        problems.append(f"{where}: '{dotted}' does not define '{name}'")
    if problems:
        return VerifyCheck("py_imports", "fail", "; ".join(problems)[:500])
    return VerifyCheck("py_imports", "pass", f"{len(py_files)} file(s)")


# Quoted for an import inside a test, bare for `python -m pytest` without pytest.
_MISSING_MODULE = re.compile(r"No module named ['\"]?([\w.]+)")


def _classify_collect_failure(output: str, root: Path) -> VerifyCheck:
    """Decide whether a pytest collection failure is a real bug (fail) or an
    environmental missing-dependency (warn).

    - `cannot import name X from Y`  → the module exists but the symbol doesn't:
      the coder's code doesn't hang together → FAIL.
    - `No module named 'X'`          → FAIL if X is a local module (coder referenced
      something it never wrote), else WARN (third-party dep not installed here).
    - other import-time errors (SyntaxError / NameError / …) → FAIL.
    - anything we can't recognize    → WARN (stay conservative; don't block on noise).
    """
    tail = output.strip()[-500:]
    if "cannot import name" in output:
        return VerifyCheck("pytest_collect", "fail", tail)

    m = _MISSING_MODULE.search(output)
    if m:
        top = m.group(1).split(".")[0]
        if _is_local_module(top, root):
            return VerifyCheck("pytest_collect", "fail", f"missing local module '{m.group(1)}': {tail}")
        return VerifyCheck("pytest_collect", "warn", f"missing dependency '{top}' (not local): {tail}")

    if re.search(r"\b(SyntaxError|NameError|IndentationError|AttributeError|TypeError)\b", output):
        return VerifyCheck("pytest_collect", "fail", tail)

    return VerifyCheck("pytest_collect", "warn", tail)


def run_tests(root: Path) -> VerifyCheck:
    """Actually run the materialized tests (opt-in). A failure HARD-fails the gate.

    Not a hardened security sandbox — it's a subprocess with a timeout. Gated by
    `tests_enabled()` so untrusted generated code is never executed by default.
    """
    if not tests_enabled():
        return VerifyCheck("pytest_run", "skip", "disabled (set FACTORY_RUN_TESTS=1 to run)")
    python = product_python(root)
    if not _has_pytest(python):
        return VerifyCheck("pytest_run", "skip", "pytest not installed")
    try:
        proc = subprocess.run(
            [python, "-m", "pytest", "-q", str(root)],
            cwd=str(root), capture_output=True, text=True, timeout=suite_timeout(),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return VerifyCheck("pytest_run", "fail", f"tests timed out after {suite_timeout()}s ({python})")
    if proc.returncode != 0:
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        tail = output.strip()[-600:]
        verdict = _classify_collect_failure(output, root)
        if verdict.status == "warn" and _MISSING_MODULE.search(output):
            # The product's interpreter lacks a dependency (or pytest): an environment
            # problem, not the coder's bug — and the tests never ran, so never a pass.
            return VerifyCheck("pytest_run", "warn", f"tests did not run: {python} {verdict.detail}")
        return VerifyCheck("pytest_run", "fail", f"with {python}: {tail}")
    return VerifyCheck("pytest_run", "pass", f"tests passed (with {python})")


def is_py_test(path: Path) -> bool:
    n = path.name
    return n.startswith("test_") or n.endswith("_test.py")


# Directories that are never the project's own test suite.
_SKIP_TREES = {".git", ".venv", "venv", "__pycache__", ".opencode", ".sandbox", "node_modules"}


def has_tests(root: Path) -> bool:
    """Whether the repo contains any Python test file at all.

    Guards `run_tests`: a repo with no suite has nothing to run, and pytest's
    "no tests collected" exit code would otherwise fail the gate for a greenfield
    first task that legitimately has no tests yet.
    """
    try:
        for path in root.rglob("*.py"):
            if any(part in _SKIP_TREES for part in path.parts):
                continue
            if is_py_test(path):
                return True
    except OSError:
        return False
    return False
