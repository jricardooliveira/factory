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
  fails the gate, and the repo's suite runs after EVERY attempt, whatever files
  it wrote. Off by default since executing agent-generated code is risky
  without true OS-level isolation.

Layout: shared types and the subprocess runner in base.py, one module per
toolchain (python, go, typescript), plus the scope policy (scope.py).
Git plumbing lives in factory.workspace.git — it measures the workspace, it does
not judge it.
"""

from __future__ import annotations

from pathlib import Path

from factory.verification import go, python, typescript
from factory.verification.base import VerifyCheck, VerifyResult, tests_enabled

__all__ = ["VerifyCheck", "VerifyResult", "tests_enabled", "verify_changes"]


def verify_changes(written_paths: list[Path], *, root: Path) -> VerifyResult:
    """Verify materialized files parse/typecheck. Returns a structured result."""
    result = VerifyResult()
    paths = [Path(p) for p in written_paths]

    py_files = [p for p in paths if p.suffix == ".py"]
    js_files = [p for p in paths if p.suffix in {".js", ".mjs", ".cjs"}]
    ts_files = [p for p in paths if p.suffix in {".ts", ".tsx"}]
    go_files = [p for p in paths if p.suffix == ".go"]

    if py_files:
        result.checks.append(python.py_compile_check(py_files, root))
        # Static: catches a coder referencing a module or name it never wrote,
        # without importing — i.e. without executing — anything it wrote.
        result.checks.append(python.static_import_check(py_files, root))
        # No `pytest --collect-only` by default: collection imports conftest.py and
        # every test module, so it EXECUTED agent-written code on the operator's
        # machine with FACTORY_RUN_TESTS off (review task T03). Opting in to tests
        # is opting in to running that code.

    if go_files:
        module_dirs = go.go_module_dirs(root, go_files)
        build = go.go_build(module_dirs)
        result.checks.append(build)
        # No module means `go build` could not run. Fall back to a parse check
        # rather than letting the code through unverified; the missing manifest
        # stays a warning so the operator still hears about it.
        if not module_dirs:
            result.checks.append(go.go_parse(go_files, root))
        # vet and the test suite both need a building package; running them on a
        # broken build only produces a second copy of the same error.
        if build.status == "pass":
            result.checks.append(go.go_vet(module_dirs))
            if tests_enabled():
                result.checks.append(go.run_go_tests(module_dirs))

    if js_files:
        result.checks.append(typescript.node_check(js_files, root))

    if ts_files:
        result.checks.append(typescript.tsc_check(root, ts_files))

    if tests_enabled():
        # Which suite exists is a fact about the REPOSITORY, not about the files this
        # attempt wrote: a template-only retry (or one that wrote nothing) used to
        # get a lone `verify:skip` — a pass — straight after a `pytest_run:fail`.
        if python.has_tests(root):
            result.checks.append(python.run_tests(root))
        # A Go attempt already ran its modules' tests above (they need its build).
        if not go_files and (test_modules := go.modules_with_tests(root)):
            result.checks.append(go.run_go_tests(test_modules))

    if not result.checks:
        result.checks.append(VerifyCheck("verify", "skip", "no verifiable files"))

    return result
