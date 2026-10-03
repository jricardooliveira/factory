"""Checked Git operations for isolated candidates. No silent success on Git errors."""
from __future__ import annotations

import fcntl
import hashlib
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class WorkspaceError(RuntimeError):
    pass


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ['git', *args], cwd=root, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode:
        raise WorkspaceError(f"git {args[0]} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


@contextmanager
def project_lock(db_path: Path, project_id: str) -> Iterator[None]:
    """Cross-process publication/integration lock; never hold a database transaction here."""
    folder = db_path.resolve().parent / 'locks'
    folder.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(project_id.encode()).hexdigest()
    with (folder / f'{key}.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def create_workspace(source: Path, target: Path, branch: str, baseline: str) -> Path:
    """An existing directory is never adopted as a new job's workspace."""
    resolved = git(source, 'rev-parse', '--verify', f'{baseline}^{{commit}}')
    if resolved != baseline:
        raise WorkspaceError('Workspace baseline must be a full immutable commit ID')
    if target.exists():
        raise WorkspaceError(f'Workspace already exists: {target}; reconcile it before retrying')
    target.parent.mkdir(parents=True, exist_ok=True)
    git(source, 'worktree', 'add', '-b', branch, str(target), baseline)
    from factory.workspace.projects import link_opencode_agents
    from factory.workspace.git import _exclude_factory_infra
    _exclude_factory_infra(target)
    link_opencode_agents(target)
    return target


def merge_candidate(root: Path, commit: str) -> str:
    """Merge immutable reviewed content; leave conflicts visible for reconciliation."""
    git(root, '-c', 'user.name=factory', '-c', 'user.email=factory@local',
        '-c', 'commit.gpgsign=false', 'merge', '--no-edit', '--no-ff', commit)
    return git(root, 'rev-parse', 'HEAD')


def clean(root: Path) -> bool:
    return not git(root, 'status', '--porcelain', '--untracked-files=all')
