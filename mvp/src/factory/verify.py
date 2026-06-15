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
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_TIMEOUT = 60
_TEST_TIMEOUT = 180


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


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=_TIMEOUT
    )


def is_git_repo(root: Path) -> bool:
    return (root / ".git").exists()


def git_init(root: Path) -> None:
    """Initialize a git repo at root if not already one (best-effort)."""
    if is_git_repo(root) or shutil.which("git") is None:
        return
    try:
        _run(["git", "init", "-q"], cwd=root)
    except (subprocess.SubprocessError, OSError):
        return


def git_commit_all(root: Path, message: str) -> bool:
    """Commit all current changes in the repo (best-effort).

    Used to checkpoint each completed task so the NEXT task's `git status` shows
    only its own new files — otherwise the governance/scope check would see prior
    tasks' files as undeclared out-of-band writes. Returns True if a commit was made.
    """
    if not is_git_repo(root) or shutil.which("git") is None:
        return False
    try:
        _run(["git", "add", "-A"], root)
        proc = _run(
            ["git", "-c", "user.name=factory", "-c", "user.email=factory@local",
             "commit", "-m", message, "--no-gpg-sign"],
            root,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


# Git's well-known empty-tree object — a valid "diff from nothing" baseline.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_DIFF_MAX_CHARS = 16000


def _git_resolve(root: Path, ref: str) -> str | None:
    """Resolve a git ref to a sha, or None if it doesn't exist."""
    try:
        proc = _run(["git", "rev-parse", "--verify", "--quiet", ref], root)
    except (subprocess.SubprocessError, OSError):
        return None
    sha = proc.stdout.strip()
    return sha if sha else None


def _factory_baseline(root: Path) -> str:
    """The pre-factory commit to diff against: the parent of the OLDEST commit the
    factory authored (message prefix ``factory:``). Falls back to the empty tree
    when there is no such parent (factory commit is the repo root) or no factory
    commit at all."""
    try:
        proc = _run(["git", "log", "--reverse", "--format=%H%x1f%s"], root)
    except (subprocess.SubprocessError, OSError):
        return _EMPTY_TREE
    if proc.returncode != 0:
        return _EMPTY_TREE
    for line in proc.stdout.splitlines():
        sha, _, subject = line.partition("\x1f")
        if subject.startswith("factory:"):
            return _git_resolve(root, f"{sha}^") or _EMPTY_TREE
    return _EMPTY_TREE


def collect_repo_diff(root: Path, *, max_chars: int = _DIFF_MAX_CHARS) -> str | None:
    """Real cumulative diff of the factory's changes, for the tester to review.

    Returns the git diff from the pre-factory baseline to the current repo state
    (committed + any working-tree changes), so the tester sees EVERY task's change
    — not just the last one's self-report. None when not a git repo (caller then
    falls back to the agent's self-reported implementation); ``""`` when the repo
    is clean. Truncated to ``max_chars`` to bound prompt cost.
    """
    if not is_git_repo(root) or shutil.which("git") is None:
        return None
    if _git_resolve(root, "HEAD") is None:
        return None  # no commits yet — nothing to diff
    base = _factory_baseline(root)
    try:
        committed = _run(["git", "diff", base, "HEAD"], root)
        working = _run(["git", "diff"], root)
    except (subprocess.SubprocessError, OSError):
        return None
    parts = [p.stdout for p in (committed, working) if p.returncode == 0 and p.stdout.strip()]
    diff = "\n".join(parts).strip()
    if len(diff) > max_chars:
        diff = diff[:max_chars] + f"\n... [diff truncated at {max_chars} chars]"
    return diff


def git_changed_paths(root: Path) -> list[str] | None:
    """Return changed paths from `git status --porcelain`, or None if not a repo."""
    if not is_git_repo(root) or shutil.which("git") is None:
        return None
    try:
        # --untracked-files=all lists individual files (not collapsed dirs) so the
        # diff is comparable to the agent's per-file claims. Project repos are tiny.
        proc = _run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root)
    except (subprocess.SubprocessError, OSError):
        return None
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        # format: "XY <path>" (XY = 2-char status)
        path = line[3:].strip().strip('"')
        if not path or "__pycache__" in path or path.endswith((".pyc", ".pyo")):
            continue
        # Infra/tooling noise — not application code the agent claims to author.
        if path.split("/", 1)[0] in {".opencode", ".git", ".sandbox", ".venv"}:
            continue
        paths.append(path)
    return paths


def _py_compile(py_files: list[Path], root: Path) -> VerifyCheck:
    try:
        proc = _run([sys.executable, "-m", "py_compile", *[str(p) for p in py_files]], root)
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


def _pytest_collect(root: Path) -> VerifyCheck | None:
    if importlib.util.find_spec("pytest") is None:
        return VerifyCheck("pytest_collect", "skip", "pytest not installed")
    try:
        proc = _run([sys.executable, "-m", "pytest", "--collect-only", "-q"], root)
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
            cwd=str(root), capture_output=True, text=True, timeout=_TEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return VerifyCheck("pytest_run", "fail", f"tests timed out after {_TEST_TIMEOUT}s")
    if proc.returncode != 0:
        return VerifyCheck("pytest_run", "fail", (proc.stdout or proc.stderr).strip()[-600:])
    return VerifyCheck("pytest_run", "pass", "tests passed")


def _node_check(js_files: list[Path], root: Path) -> VerifyCheck:
    if shutil.which("node") is None:
        return VerifyCheck("node_check", "skip", "node not installed")
    for f in js_files:
        try:
            proc = _run(["node", "--check", str(f)], root)
        except subprocess.TimeoutExpired:
            return VerifyCheck("node_check", "warn", "timed out")
        if proc.returncode != 0:
            return VerifyCheck("node_check", "fail", proc.stderr.strip()[:500])
    return VerifyCheck("node_check", "pass", f"{len(js_files)} file(s)")


def _tsc_check(root: Path) -> VerifyCheck:
    if shutil.which("tsc") is None:
        return VerifyCheck("tsc", "skip", "tsc not installed")
    try:
        proc = _run(["tsc", "--noEmit"], root)
    except subprocess.TimeoutExpired:
        return VerifyCheck("tsc", "warn", "timed out")
    if proc.returncode != 0:
        return VerifyCheck("tsc", "fail", proc.stdout.strip()[:500])
    return VerifyCheck("tsc", "pass", "no type errors")


def _is_py_test(path: Path) -> bool:
    n = path.name
    return n.startswith("test_") or n.endswith("_test.py")


def verify_changes(written_paths: list[Path], *, root: Path) -> VerifyResult:
    """Verify materialized files parse/typecheck. Returns a structured result."""
    result = VerifyResult()
    paths = [Path(p) for p in written_paths]

    py_files = [p for p in paths if p.suffix == ".py"]
    js_files = [p for p in paths if p.suffix in {".js", ".mjs", ".cjs"}]
    ts_files = [p for p in paths if p.suffix in {".ts", ".tsx"}]

    if py_files:
        result.checks.append(_py_compile(py_files, root))
        if any(_is_py_test(p) for p in py_files):
            # Run tests when opted in (hard gate); otherwise collect-only (warn).
            if tests_enabled():
                result.checks.append(run_tests(root))
            else:
                collect = _pytest_collect(root)
                if collect is not None:
                    result.checks.append(collect)

    if js_files:
        result.checks.append(_node_check(js_files, root))

    if ts_files:
        result.checks.append(_tsc_check(root))

    if not result.checks:
        result.checks.append(VerifyCheck("verify", "skip", "no verifiable files"))

    return result
