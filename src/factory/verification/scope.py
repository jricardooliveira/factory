"""Scope policy — both of the factory's scope checks, as pure set logic.

- `declared_scope_mismatch` / `scope_note`: what the coder DECLARED in
  code_blocks vs what actually changed. An undeclared change is an out-of-band
  write and BLOCKS gate-build (the coder node supplies the measured change set).
- `paths_outside_scope`: paths vs the task's declared ``scope``. The coder node
  REFUSES out-of-scope code_blocks before writing them (`nodes/coder._outside_scope`);
  the trust package also reports `scope_violations` as evidence.

The measuring (git) lives in `factory.workspace.git`; this module only judges.
"""

from __future__ import annotations

import re

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
    # A Python package cannot exist without its marker: a task scoped to
    # `calc/models.py` must create `calc/__init__.py` (found by the replay corpus).
    "__init__.py",
})


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path))


def strip_repo_prefix(path: str) -> str:
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
        strip_repo_prefix(s).rstrip("/") for s in allowed_scope if s
    ]
    violations: list[str] = []
    for raw in changed:
        # Both sides in the same space: a path an agent wrote as `repo/cli.py` in a
        # working directory not named `repo` keeps its prefix (found on a REAL run).
        path = strip_repo_prefix(raw)
        if _TEST_PATH_RE.search(path):
            continue
        if path.rsplit("/", 1)[-1] in _MANIFEST_NAMES:
            continue
        ok = any(
            path == p or path.startswith(p + "/") or fnmatch.fnmatch(path, p)
            for p in prefixes
        )
        if not ok:
            violations.append(raw)
    return violations


def declared_scope_mismatch(
    changed: set[str], claimed: set[str]
) -> tuple[set[str], set[str]]:
    """Return (unclaimed, missing) for one coder pass.

    `unclaimed` = files that changed but the coder did NOT declare in code_blocks —
    out-of-band writes, an agent scribbling outside the factory's controlled
    materialize path. `missing` = declared but not on disk.
    """
    return changed - claimed, claimed - changed


def scope_note(unclaimed: set[str], missing: set[str]) -> str | None:
    """The gate-build reason suffix for a declared-scope mismatch (None if clean)."""
    if not unclaimed and not missing:
        return None
    bits = []
    if unclaimed:
        bits.append(f"unclaimed: {sorted(unclaimed)}")
    if missing:
        bits.append(f"claimed-but-absent: {sorted(missing)}")
    return "scope-mismatch (" + "; ".join(bits) + ")"
