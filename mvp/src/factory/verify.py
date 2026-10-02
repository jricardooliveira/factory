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
        # reading it would block the gate. See opencode_client.run_agent.
        stdin=subprocess.DEVNULL,
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
    _exclude_factory_infra(root)


# Factory plumbing that lives inside a product repo but is not part of the product.
# `.opencode` is the symlink `projects._link_opencode_agents` creates so opencode can
# resolve the agent definitions; it is an absolute path to the operator's machine.
_INFRA_EXCLUDES = ("/.opencode",)


def _exclude_factory_infra(root: Path) -> None:
    """Keep factory plumbing out of the product's git history (idempotent).

    Uses `.git/info/exclude`, not the product's `.gitignore`: the factory has no
    business editing the product's own files to hide its own. Re-asserted on every
    commit because `git_init` is a no-op on a repo that already exists.

    Without it, `git add -A` committed the `.opencode` symlink into the generated
    app; it then appeared in the diff the tester and remediation coder review, the
    coder echoed it back as a code block, and the run failed.
    """
    exclude = root / ".git" / "info" / "exclude"
    try:
        existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        missing = [e for e in _INFRA_EXCLUDES if e not in existing.splitlines()]
        if missing:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            sep = "" if not existing or existing.endswith("\n") else "\n"
            exclude.write_text(existing + sep + "\n".join(missing) + "\n", encoding="utf-8")
    except OSError:
        return


def git_commit_all(root: Path, message: str) -> bool:
    """Commit all current changes in the repo (best-effort).

    Used to checkpoint each completed task so the NEXT task's `git status` shows
    only its own new files — otherwise the governance/scope check would see prior
    tasks' files as undeclared out-of-band writes. Returns True if a commit was made.
    """
    if not is_git_repo(root) or shutil.which("git") is None:
        return False
    _exclude_factory_infra(root)
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


def git_head(root: Path) -> str | None:
    """The repo's current commit sha, or None if not a git repo / no commits.

    Captured at run start as the run's own baseline, so the trust package can
    measure THIS run's change set instead of every factory commit ever made.
    """
    if not is_git_repo(root) or shutil.which("git") is None:
        return None
    return _git_resolve(root, "HEAD")


# git --name-status letters → the schema's change vocabulary.
_STATUS_MAP = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "added"}


def git_changed_files(root: Path, base: str | None) -> list[dict[str, str]] | None:
    """Real per-file change set from `base` to the current state, or None if it
    cannot be measured from git.

    Returns ``[{"path": ..., "change": "added|modified|deleted|renamed"}]``. This
    is the trust package's Evidence 2: what git saw, not what the agent claimed.
    A file both committed and then edited appears once, with its committed status.
    """
    if not is_git_repo(root) or shutil.which("git") is None:
        return None
    if _git_resolve(root, "HEAD") is None:
        return None
    baseline = base or _factory_baseline(root)
    try:
        committed = _run(["git", "diff", "--name-status", baseline, "HEAD"], root)
        working = _run(["git", "diff", "--name-status"], root)
        untracked = _run(["git", "ls-files", "--others", "--exclude-standard"], root)
    except (subprocess.SubprocessError, OSError):
        return None
    if committed.returncode != 0:
        return None

    files: dict[str, str] = {}
    for proc in (committed, working):
        if proc.returncode != 0:
            continue
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            # Rename lines are "R100\told\tnew" — the new path is what changed.
            status, path = parts[0][:1], parts[-1].strip()
            if _is_noise(path):
                continue
            files.setdefault(path, _STATUS_MAP.get(status, "modified"))
    if untracked.returncode == 0:
        for path in untracked.stdout.splitlines():
            path = path.strip()
            if path and not _is_noise(path):
                files.setdefault(path, "added")
    return [{"path": p, "change": c} for p, c in sorted(files.items())]


def _is_noise(path: str) -> bool:
    """Infra/tooling paths that are not application code the factory authored."""
    if not path or "__pycache__" in path or path.endswith((".pyc", ".pyo")):
        return True
    return path.split("/", 1)[0] in {".opencode", ".git", ".sandbox", ".venv"}


# A test file is never a scope violation on its own: the coder is *required* to
# add tests (mandatory happy-path coverage), and a task's declared scope names
# the source it may touch, not the tests that prove it.
_TEST_PATH_RE = re.compile(r"(^|/)(tests?)(/|$)|(^|/)test_[^/]+$|_test\.[a-z]+$")

# Files the TOOLCHAIN structurally requires at a fixed location, which a task can
# therefore not avoid creating. Go resolves packages from the directory holding
# go.mod, so a task scoped to `backend/internal/domain/` must still write
# `backend/go.mod` — reporting that as scope creep flagged every Go story for a
# file it had no choice about, and a check that cries wolf gets ignored.
_MANIFEST_NAMES = frozenset({
    "go.mod", "go.sum", "go.work",
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "tsconfig.json", "nuxt.config.ts", "vite.config.ts", "vitest.config.ts",
    "pyproject.toml", "requirements.txt", "uv.lock", "setup.cfg",
    "Makefile", "Dockerfile", ".gitignore",
})


def _strip_repo_prefix(path: str) -> str:
    """Drop a leading ``repo/`` (or ``./repo/``), mirroring normalize_block_path.

    A factory-wide convention: agents write repo-rooted paths, and the repo root
    is the working directory. Both sides of the scope comparison must be in the
    same space or nothing ever matches.
    """
    for prefix in ("repo/", "./repo/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def paths_outside_scope(changed: list[str], allowed_scope: list[str]) -> list[str]:
    """Changed paths that fall outside a task's declared allowed scope.

    Closes the gap between EFFECTIVENESS.md §6 ("the real git diff is checked
    against the pack's allowed scope") and the code, where ``task.scope`` was
    rendered into the prompt and then never compared to anything.

    Scope entries may be a directory prefix (``src/users/``), an exact path, or a
    glob (``src/users/*.py``). An EMPTY scope declares nothing and therefore
    forbids nothing — otherwise every unscoped task would look like a breach.
    """
    if not allowed_scope:
        return []
    import fnmatch

    # Agents emit repo-rooted scope (`repo/backend/internal/domain`) because the
    # factory tells them to use repo-relative paths, while the paths actually
    # written have had that prefix stripped by `materialize.normalize_block_path`
    # (the working directory IS the repo). Comparing the two spaces directly made
    # every file on every real project run a violation.
    prefixes = [
        _strip_repo_prefix(s).rstrip("/") for s in allowed_scope if s
    ]
    violations: list[str] = []
    for path in changed:
        if _TEST_PATH_RE.search(path):
            continue
        if path.rsplit("/", 1)[-1] in _MANIFEST_NAMES:
            continue
        ok = any(
            path == p or path.startswith(p + "/") or fnmatch.fnmatch(path, p)
            for p in prefixes
        )
        if not ok:
            violations.append(path)
    return violations


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
            stdin=subprocess.DEVNULL,
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


def _ts_project_dirs(root: Path, ts_files: list[Path]) -> list[Path]:
    """Nearest tsconfig.json ancestor for each changed .ts file (deduped).

    A monorepo's TS project is `frontend/`, not the repo root — every Nuxt layout
    puts it there. Running tsc from the root instead is what made this check
    useless (see _tsc_check).
    """
    root_resolved = root.resolve()
    found: list[Path] = []
    for path in ts_files:
        candidate = (root / path) if not path.is_absolute() else path
        directory = candidate.resolve().parent
        while True:
            if (directory / "tsconfig.json").is_file():
                if directory not in found:
                    found.append(directory)
                break
            if directory == root_resolved or root_resolved not in directory.parents:
                break
            directory = directory.parent
    return found


def _resolve_tsc(project_dir: Path, root: Path) -> list[str] | None:
    """The tsc to use, preferring the project's own pinned copy.

    A Nuxt app pins its TypeScript version; typechecking it with whatever `tsc`
    happens to be on PATH can produce errors the project itself would never see.
    """
    for base in (project_dir, root):
        local = base / "node_modules" / ".bin" / "tsc"
        if local.is_file():
            return [str(local)]
    if shutil.which("tsc") is not None:
        return ["tsc"]
    return None


def _tsc_check(root: Path, ts_files: list[Path]) -> VerifyCheck:
    """Typecheck each TS project that owns a changed file.

    Previously this ran `tsc --noEmit` from the repo root with no `-p`. With no
    tsconfig.json at the root, tsc prints its HELP TEXT and exits 1 — which the
    gate recorded as `tsc:fail`, blocking the build for a reason unrelated to the
    code. It failed on valid TypeScript too. A false block is worse than a false
    pass: it spends the remediation budget re-fixing code that was never broken.
    """
    project_dirs = _ts_project_dirs(root, ts_files)
    if not project_dirs:
        # A loose .ts file with no project cannot be meaningfully typechecked;
        # that is missing configuration, not broken code.
        return VerifyCheck("tsc", "warn", "no tsconfig.json found for the changed .ts files")
    for project_dir in project_dirs:
        tsc = _resolve_tsc(project_dir, root)
        if tsc is None:
            return VerifyCheck("tsc", "skip", "tsc not installed")
        try:
            proc = _run([*tsc, "--noEmit", "-p", str(project_dir)], root, timeout=_GO_TIMEOUT)
        except subprocess.TimeoutExpired:
            return VerifyCheck("tsc", "warn", "timed out")
        if proc.returncode != 0:
            output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            # A project whose dependencies were never installed cannot be
            # typechecked — that is an environment gap, like a missing Python dep.
            if "Cannot find module" in output or "Cannot find type definition" in output:
                return VerifyCheck("tsc", "warn", f"dependencies not installed: {output[-200:]}")
            return VerifyCheck("tsc", "fail", output[:600])
    return VerifyCheck("tsc", "pass", f"{len(project_dirs)} project(s), no type errors")


# ── Go ────────────────────────────────────────────────────────────
# The challenge-driving gap: `verify_changes` inferred its toolchain from file
# extensions and knew only .py/.js/.ts, so on a Go backend it emitted
# "no verifiable files", gate-build PASSED, and a story could be reported
# complete without anything ever establishing that the code compiles.


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


def _go_module_dirs(root: Path, go_files: list[Path]) -> list[Path]:
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


def _go_build(module_dirs: list[Path]) -> VerifyCheck:
    if shutil.which("go") is None:
        return VerifyCheck("go_build", "skip", "go not installed")
    if not module_dirs:
        # A first task may legitimately write a .go file before go.mod exists.
        return VerifyCheck("go_build", "warn", "no go.mod found for the changed .go files")
    warnings: list[str] = []
    for module_dir in module_dirs:
        module_path = _go_module_path(module_dir)
        try:
            proc = _run(["go", "build", "./..."], module_dir, timeout=_GO_TIMEOUT)
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


def _go_parse(go_files: list[Path], root: Path) -> VerifyCheck:
    """Syntax-check .go files with NO module, using `gofmt -e`.

    `go build` requires a module, so a task that writes Go before (or without)
    a go.mod escaped verification entirely — observed live, where the coder
    produced a service package and its test and no manifest, and gate-build
    passed having checked nothing. `gofmt -e` parses individual files and needs
    no module, so there is no excuse for skipping the syntax check.
    """
    if shutil.which("gofmt") is None:
        return VerifyCheck("go_parse", "skip", "gofmt not installed")
    targets = [str((root / p) if not p.is_absolute() else p) for p in go_files]
    try:
        proc = _run(["gofmt", "-e", "-l", *targets], root)
    except (subprocess.TimeoutExpired, OSError):
        return VerifyCheck("go_parse", "warn", "gofmt did not run")
    # gofmt -e exits non-zero and writes parse errors to stderr on bad syntax;
    # a merely-unformatted (but valid) file is listed on stdout with exit 0.
    if proc.returncode != 0:
        return VerifyCheck("go_parse", "fail", (proc.stderr or proc.stdout).strip()[:600])
    return VerifyCheck("go_parse", "pass", f"{len(targets)} file(s) parse")


def _go_vet(module_dirs: list[Path]) -> VerifyCheck:
    """`go vet` catches what the compiler allows — named in the challenge's own
    quality gates (bad Printf verbs, unreachable code, lost struct tags)."""
    if shutil.which("go") is None:
        return VerifyCheck("go_vet", "skip", "go not installed")
    if not module_dirs:
        return VerifyCheck("go_vet", "skip", "no go module")
    for module_dir in module_dirs:
        try:
            proc = _run(["go", "vet", "./..."], module_dir, timeout=_GO_TIMEOUT)
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
            proc = _run(["go", "test", "./..."], module_dir, timeout=_TEST_TIMEOUT)
        except subprocess.TimeoutExpired:
            return VerifyCheck("go_test", "fail", f"tests timed out after {_TEST_TIMEOUT}s")
        if proc.returncode != 0:
            output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            if _classify_go_failure(output, _go_module_path(module_dir)) == "warn":
                return VerifyCheck("go_test", "warn", output[-300:])
            return VerifyCheck("go_test", "fail", output[-600:])
    return VerifyCheck("go_test", "pass", "tests passed")


def _is_py_test(path: Path) -> bool:
    n = path.name
    return n.startswith("test_") or n.endswith("_test.py")


# Directories that are never the project's own test suite.
_SKIP_TREES = {".git", ".venv", "venv", "__pycache__", ".opencode", ".sandbox", "node_modules"}


def _has_tests(root: Path) -> bool:
    """Whether the repo contains any Python test file at all.

    Guards `run_tests`: a repo with no suite has nothing to run, and pytest's
    "no tests collected" exit code would otherwise fail the gate for a greenfield
    first task that legitimately has no tests yet.
    """
    try:
        for path in root.rglob("*.py"):
            if any(part in _SKIP_TREES for part in path.parts):
                continue
            if _is_py_test(path):
                return True
    except OSError:
        return False
    return False


def verify_changes(written_paths: list[Path], *, root: Path) -> VerifyResult:
    """Verify materialized files parse/typecheck. Returns a structured result."""
    result = VerifyResult()
    paths = [Path(p) for p in written_paths]

    py_files = [p for p in paths if p.suffix == ".py"]
    js_files = [p for p in paths if p.suffix in {".js", ".mjs", ".cjs"}]
    ts_files = [p for p in paths if p.suffix in {".ts", ".tsx"}]
    go_files = [p for p in paths if p.suffix == ".go"]

    if py_files:
        result.checks.append(_py_compile(py_files, root))
        # Run the project's suite after ANY Python change, not only after a task
        # that happened to write a test file. Gating on "this task wrote a test"
        # meant a source-only change never executed the existing tests — exactly
        # the case where a regression is invisible.
        if tests_enabled():
            if _has_tests(root):
                result.checks.append(run_tests(root))
        else:
            # Collect-only is safe without a sandbox; it still catches a coder
            # referencing a module it never wrote.
            if any(_is_py_test(p) for p in py_files) or _has_tests(root):
                collect = _pytest_collect(root)
                if collect is not None:
                    result.checks.append(collect)

    if go_files:
        module_dirs = _go_module_dirs(root, go_files)
        build = _go_build(module_dirs)
        result.checks.append(build)
        # No module means `go build` could not run. Fall back to a parse check
        # rather than letting the code through unverified; the missing manifest
        # stays a warning so the operator still hears about it.
        if not module_dirs:
            result.checks.append(_go_parse(go_files, root))
        # vet and the test suite both need a building package; running them on a
        # broken build only produces a second copy of the same error.
        if build.status == "pass":
            result.checks.append(_go_vet(module_dirs))
            if tests_enabled():
                result.checks.append(run_go_tests(module_dirs))

    if js_files:
        result.checks.append(_node_check(js_files, root))

    if ts_files:
        result.checks.append(_tsc_check(root, ts_files))

    if not result.checks:
        result.checks.append(VerifyCheck("verify", "skip", "no verifiable files"))

    return result
