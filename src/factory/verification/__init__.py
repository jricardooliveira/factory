"""Deterministic post-materialization verification (no LLM).

Closes the silent-failure hole: a coder run is not "complete" just because files
were written — the code must at least parse / typecheck. Toolchain is inferred
from the materialized file extensions.

Design notes:
- Hard FAIL only on definitive syntax/compile errors (py_compile, node --check,
  tsc --noEmit). That is the actual hole we're closing.
- WARN (never block) when a toolchain or dependency is unavailable, or when
  pytest collection fails — collection imports project deps that may not be
  installed in the factory's environment, so a failure there is not proof the
  code is broken.
- Running test BODIES is opt-in (FACTORY_RUN_TESTS=1): a test failure then HARD-
  fails the gate. Off by default since executing agent-generated code is risky
  without true OS-level isolation.

Layout: one module per toolchain (python, go, typescript) plus the scope policy
(scope.py). Git plumbing lives in factory.workspace.git — it measures the
workspace, it does not judge it.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_TIMEOUT = 60
_TEST_TIMEOUT = 180
# `go build` may resolve and download modules on a cold cache, so it gets more
# room than the 60s a py_compile needs.
_GO_TIMEOUT = 180


def tests_enabled() -> bool:
    """Whether to actually RUN materialized tests (opt-in).

    Off by default: executing agent-generated code is risky without true OS-level
    isolation. Operators who trust their setup opt in with FACTORY_RUN_TESTS=1.
    """
    return os.environ.get("FACTORY_RUN_TESTS", "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class VerifyCheck:
    name: str
    status: str  # "pass" | "fail" | "warn" | "skip"
    detail: str = ""


@dataclass
class VerifyResult:
    checks: list[VerifyCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(c.status == "fail" for c in self.checks)

    @property
    def verdict(self) -> str:
        if any(c.status == "fail" for c in self.checks):
            return "fail"
        if any(c.status == "warn" for c in self.checks):
            return "warn"
        return "pass"

    @property
    def summary(self) -> str:
        if not self.checks:
            return "no verifiable files"
        parts = [f"{c.name}:{c.status}" for c in self.checks]
        return ", ".join(parts)


def _run(
    cmd: list[str], cwd: Path, timeout: int = _TIMEOUT
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        # Never inherit stdin: agent-written code under test (`go test`, pytest)
        # reading it would block the gate. See adapters.opencode.run_agent.
        stdin=subprocess.DEVNULL,
    )


# Imported after the shared types above: each toolchain module imports
# VerifyCheck/_run/tests_enabled from this package.
from factory.verification import go as _go  # noqa: E402
from factory.verification import python as _python  # noqa: E402
from factory.verification import typescript as _typescript  # noqa: E402


def verify_changes(written_paths: list[Path], *, root: Path) -> VerifyResult:
    """Verify materialized files parse/typecheck. Returns a structured result."""
    result = VerifyResult()
    paths = [Path(p) for p in written_paths]

    py_files = [p for p in paths if p.suffix == ".py"]
    js_files = [p for p in paths if p.suffix in {".js", ".mjs", ".cjs"}]
    ts_files = [p for p in paths if p.suffix in {".ts", ".tsx"}]
    go_files = [p for p in paths if p.suffix == ".go"]

    if py_files:
        result.checks.append(_python._py_compile(py_files, root))
        # Run the project's suite after ANY Python change, not only after a task
        # that happened to write a test file. Gating on "this task wrote a test"
        # meant a source-only change never executed the existing tests — exactly
        # the case where a regression is invisible.
        if tests_enabled():
            if _python._has_tests(root):
                result.checks.append(_python.run_tests(root))
        else:
            # Collect-only is safe without a sandbox; it still catches a coder
            # referencing a module it never wrote.
            if any(_python._is_py_test(p) for p in py_files) or _python._has_tests(root):
                collect = _python._pytest_collect(root)
                if collect is not None:
                    result.checks.append(collect)

    if go_files:
        module_dirs = _go._go_module_dirs(root, go_files)
        build = _go._go_build(module_dirs)
        result.checks.append(build)
        # No module means `go build` could not run. Fall back to a parse check
        # rather than letting the code through unverified; the missing manifest
        # stays a warning so the operator still hears about it.
        if not module_dirs:
            result.checks.append(_go._go_parse(go_files, root))
        # vet and the test suite both need a building package; running them on a
        # broken build only produces a second copy of the same error.
        if build.status == "pass":
            result.checks.append(_go._go_vet(module_dirs))
            if tests_enabled():
                result.checks.append(_go.run_go_tests(module_dirs))

    if js_files:
        result.checks.append(_typescript._node_check(js_files, root))

    if ts_files:
        result.checks.append(_typescript._tsc_check(root, ts_files))

    if not result.checks:
        result.checks.append(VerifyCheck("verify", "skip", "no verifiable files"))

    return result
