"""Go checks: ``go build`` / ``gofmt -e`` / ``go vet`` and (opt-in) ``go test``.

The challenge-driving gap: `verify_changes` inferred its toolchain from file
extensions and knew only .py/.js/.ts, so on a Go backend it emitted
"no verifiable files", gate-build PASSED, and a story could be reported
complete without anything ever establishing that the code compiles.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from factory.verification.base import BUILD_TIMEOUT, TEST_TIMEOUT, VerifyCheck, run_command, tests_enabled


def _go_module_path(module_dir: Path) -> str:
    """The module path declared in go.mod ('' if unreadable).

    Used to tell the coder's own broken code apart from an absent third-party
    dependency: an unresolvable import UNDER this path is a real bug, one outside
    it is an environment problem.
    """
    try:
        for line in (module_dir / "go.mod").read_text(encoding="utf-8").splitlines():
            if line.startswith("module "):
                return line.split(None, 1)[1].strip()
    except (OSError, IndexError):
        return ""
    return ""


def go_module_dirs(root: Path, go_files: list[Path]) -> list[Path]:
    """Nearest go.mod ancestor for each changed .go file (deduped, stable order).

    A monorepo puts the module at `backend/go.mod`, not the repo root, so the
    build must run per module rather than once at the top.
    """
    root_resolved = root.resolve()
    found: list[Path] = []
    for path in go_files:
        candidate = (root / path) if not path.is_absolute() else path
        directory = candidate.resolve().parent
        while True:
            if (directory / "go.mod").is_file():
                if directory not in found:
                    found.append(directory)
                break
            if directory == root_resolved or root_resolved not in directory.parents:
                break
            directory = directory.parent
    if not found and (root / "go.mod").is_file():
        found.append(root_resolved)
    return found


# Messages that mean "the module graph could not be resolved from here" — a
# network/cache condition, not proof the code is wrong.
_GO_ENV_MARKERS = (
    "cannot find module providing package",
    "no required module provides package",
    "module lookup disabled",
    "missing go.sum entry",
    "dial tcp",
    "connection refused",
    "i/o timeout",
    "410 Gone",
    "unrecognized import path",
)


def _classify_go_failure(output: str, module_path: str) -> str:
    """"fail" for the coder's own broken code, "warn" for an environment problem.

    Mirrors `_classify_collect_failure` for Python. An import the coder invented
    under its OWN module path can never be fixed by a download, so it must fail;
    an unreachable third-party module is not the agent's mistake.
    """
    if module_path and f"package {module_path}/" in output:
        return "fail"
    if any(marker in output for marker in _GO_ENV_MARKERS):
        return "warn"
    return "fail"


# A check that cannot run cannot earn a pass: a missing toolchain used to report
# `skip`, and gate-build passed the files it never looked at (review task T04).
_NOT_INSTALLED = "{tool} not installed — these files cannot be verified (see `factory doctor`)"


def go_build(module_dirs: list[Path]) -> VerifyCheck:
    if shutil.which("go") is None:
        return VerifyCheck("go_build", "fail", _NOT_INSTALLED.format(tool="go"))
    if not module_dirs:
        # A first task may legitimately write a .go file before go.mod exists.
        return VerifyCheck("go_build", "warn", "no go.mod found for the changed .go files")
    warnings: list[str] = []
    for module_dir in module_dirs:
        module_path = _go_module_path(module_dir)
        try:
            proc = run_command(["go", "build", "./..."], module_dir, timeout=BUILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            warnings.append(f"{module_dir.name}: timed out")
            continue
        if proc.returncode == 0:
            continue
        output = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
        verdict = _classify_go_failure(output, module_path)
        if verdict == "fail":
            return VerifyCheck("go_build", "fail", output[-600:])
        warnings.append(f"{module_dir.name}: {output[-200:]}")
    if warnings:
        return VerifyCheck("go_build", "warn", "; ".join(warnings))
    return VerifyCheck("go_build", "pass", f"{len(module_dirs)} module(s)")


def go_parse(go_files: list[Path], root: Path) -> VerifyCheck:
    """Syntax-check .go files with NO module, using `gofmt -e`.

    `go build` requires a module, so a task that writes Go before (or without)
    a go.mod escaped verification entirely — observed live, where the coder
    produced a service package and its test and no manifest, and gate-build
    passed having checked nothing. `gofmt -e` parses individual files and needs
    no module, so there is no excuse for skipping the syntax check.
    """
    if shutil.which("gofmt") is None:
        return VerifyCheck("go_parse", "fail", _NOT_INSTALLED.format(tool="gofmt"))
    targets = [str((root / p) if not p.is_absolute() else p) for p in go_files]
    try:
        proc = run_command(["gofmt", "-e", "-l", *targets], root)
    except (subprocess.TimeoutExpired, OSError):
        return VerifyCheck("go_parse", "warn", "gofmt did not run")
    # gofmt -e exits non-zero and writes parse errors to stderr on bad syntax;
    # a merely-unformatted (but valid) file is listed on stdout with exit 0.
    if proc.returncode != 0:
        return VerifyCheck("go_parse", "fail", (proc.stderr or proc.stdout).strip()[:600])
    return VerifyCheck("go_parse", "pass", f"{len(targets)} file(s) parse")


def go_vet(module_dirs: list[Path]) -> VerifyCheck:
    """`go vet` catches what the compiler allows — named in the challenge's own
    quality gates (bad Printf verbs, unreachable code, lost struct tags)."""
    if shutil.which("go") is None:
        return VerifyCheck("go_vet", "skip", "go not installed")
    if not module_dirs:
        return VerifyCheck("go_vet", "skip", "no go module")
    for module_dir in module_dirs:
        try:
            proc = run_command(["go", "vet", "./..."], module_dir, timeout=BUILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            return VerifyCheck("go_vet", "warn", "timed out")
        if proc.returncode != 0:
            output = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
            # vet needs the package to build; a build problem is already reported
            # by go_build, so don't double-fail on it here.
            if _classify_go_failure(output, _go_module_path(module_dir)) == "warn":
                return VerifyCheck("go_vet", "warn", output[-300:])
            return VerifyCheck("go_vet", "fail", output[-600:])
    return VerifyCheck("go_vet", "pass", "no findings")


def run_go_tests(module_dirs: list[Path]) -> VerifyCheck:
    """`go test ./...` (opt-in, like the Python suite). A failure HARD-fails."""
    if not tests_enabled():
        return VerifyCheck("go_test", "skip", "disabled (set FACTORY_RUN_TESTS=1 to run)")
    if shutil.which("go") is None:
        return VerifyCheck("go_test", "skip", "go not installed")
    if not module_dirs:
        return VerifyCheck("go_test", "skip", "no go module")
    for module_dir in module_dirs:
        try:
            proc = run_command(["go", "test", "./..."], module_dir, timeout=TEST_TIMEOUT)
        except subprocess.TimeoutExpired:
            return VerifyCheck("go_test", "fail", f"tests timed out after {TEST_TIMEOUT}s")
        if proc.returncode != 0:
            output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            if _classify_go_failure(output, _go_module_path(module_dir)) == "warn":
                return VerifyCheck("go_test", "warn", output[-300:])
            return VerifyCheck("go_test", "fail", output[-600:])
    return VerifyCheck("go_test", "pass", "tests passed")
