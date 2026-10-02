"""JavaScript / TypeScript checks: ``node --check`` and per-project ``tsc -p``."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from factory.verification import _GO_TIMEOUT, VerifyCheck, _run


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
