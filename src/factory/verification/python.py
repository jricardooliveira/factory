"""Python checks: py_compile, pytest collection and (opt-in) the test suite."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

from factory.verification.base import TEST_TIMEOUT, VerifyCheck, run_command, tests_enabled


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

    m = re.search(r"No module named ['\"]([^'\"]+)['\"]", output)
    if m:
        top = m.group(1).split(".")[0]
        if _is_local_module(top, root):
            return VerifyCheck("pytest_collect", "fail", f"missing local module '{m.group(1)}': {tail}")
        return VerifyCheck("pytest_collect", "warn", f"missing dependency '{top}' (not local): {tail}")

    if re.search(r"\b(SyntaxError|NameError|IndentationError|AttributeError|TypeError)\b", output):
        return VerifyCheck("pytest_collect", "fail", tail)

    return VerifyCheck("pytest_collect", "warn", tail)


def pytest_collect(root: Path) -> VerifyCheck | None:
    if importlib.util.find_spec("pytest") is None:
        return VerifyCheck("pytest_collect", "skip", "pytest not installed")
    try:
        proc = run_command([sys.executable, "-m", "pytest", "--collect-only", "-q"], root)
    except subprocess.TimeoutExpired:
        return VerifyCheck("pytest_collect", "warn", "timed out")
    if proc.returncode != 0:
        # A real bug in the coder's own code must FAIL; a merely-absent third-party
        # dep stays a WARN. Distinguishing the two closes a silent-failure crack.
        return _classify_collect_failure((proc.stdout or "") + "\n" + (proc.stderr or ""), root)
    return VerifyCheck("pytest_collect", "pass", "collected")


def run_tests(root: Path) -> VerifyCheck:
    """Actually run the materialized tests (opt-in). A failure HARD-fails the gate.

    Not a hardened security sandbox — it's a subprocess with a timeout. Gated by
    `tests_enabled()` so untrusted generated code is never executed by default.
    """
    if not tests_enabled():
        return VerifyCheck("pytest_run", "skip", "disabled (set FACTORY_RUN_TESTS=1 to run)")
    if importlib.util.find_spec("pytest") is None:
        return VerifyCheck("pytest_run", "skip", "pytest not installed")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(root)],
            cwd=str(root), capture_output=True, text=True, timeout=TEST_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return VerifyCheck("pytest_run", "fail", f"tests timed out after {TEST_TIMEOUT}s")
    if proc.returncode != 0:
        return VerifyCheck("pytest_run", "fail", (proc.stdout or proc.stderr).strip()[-600:])
    return VerifyCheck("pytest_run", "pass", "tests passed")


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
