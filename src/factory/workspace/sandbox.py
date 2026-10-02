"""Where a run's code lives, and the scratch repositories replays work in.

`factory replay` re-drives a past run's orchestration on its frozen agent
outputs. It used to do that IN the live product repository: the coder
materialized code there, and the evidence writers re-stamped and re-committed
INTENT/SPEC/PLAN/ADR with `factory:` subjects — moving the product's HEAD and
rewriting the history of a run that had already passed its checkpoints. A
non-project replay wrote into whatever directory `factory replay` was run from.

A replay now works in its own clone, ``<home>/replays/run-<id>/``, checked out at
the commit the replayed run started from. The product is never touched, the
replay is still faithful (same code, same project memory), and what it produced
stays inspectable afterwards. A resume of a parked replay reuses the same clone.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from factory.workspace import layout
from factory.workspace.git import is_git_repo


def replay_sandbox(run_id: int, db_path: Path) -> Path:
    """The scratch repository for replay run `run_id` (it may not exist yet)."""
    return layout.replay_dir(run_id, Path(db_path).expanduser().absolute().parent)


def prepare_replay_sandbox(
    run_id: int, db_path: Path, *, source: Path | None, commit: str | None
) -> Path:
    """Create (or reuse) the replay run's scratch repository and return it.

    A git `source` is cloned and checked out at `commit` (the replayed run's
    baseline, when it recorded one). Without one — a non-project run, whose
    original working directory was never recorded — the sandbox is an empty
    directory, as a fresh working directory would have been.
    """
    target = replay_sandbox(run_id, db_path)
    if target.is_dir():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if source is not None and is_git_repo(source) and shutil.which("git"):
        _git(["git", "clone", "--quiet", "--no-hardlinks", str(source), str(target)], None)
        if commit:
            _git(["git", "checkout", "--quiet", "-B", f"replay-{run_id}", commit], target)
    target.mkdir(parents=True, exist_ok=True)
    return target


def run_repository(run: dict[str, Any], db_path: Path) -> Path | None:
    """The repository a run's code went into: its replay sandbox, else its
    project's repository (resolved for this DB), else None (a non-project run)."""
    if run.get("replay_of"):
        sandbox = replay_sandbox(run["id"], db_path)
        return sandbox if sandbox.is_dir() else None
    return layout.resolve_location(run.get("repo_path"), db_path)


def _git(cmd: list[str], cwd: Path | None) -> None:
    try:
        subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
            timeout=120, stdin=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError):
        return
