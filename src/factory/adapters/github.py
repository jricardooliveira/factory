"""GitHub through the `gh` CLI: open and merge a story's pull request.

Release = merged PR (operator decision, 2026-10-02). Used only for a product repo
whose `origin` is on GitHub; failures are returned, never raised, so the caller can
name them at Checkpoint 3 instead of crashing a run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_TIMEOUT = 120


def _gh(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Never inherit stdin: a gh prompt would block the pipeline.
    return subprocess.run(["gh", *args], cwd=str(repo), capture_output=True, text=True,
                          timeout=_TIMEOUT, stdin=subprocess.DEVNULL)


def open_pull_request(
    repo: Path, branch: str, target: str, title: str, body: str
) -> tuple[str | None, str | None]:
    """(url, None) for the branch's pull request — reused if it is already open —
    or (None, error)."""
    try:
        existing = _gh(repo, "pr", "view", branch, "--json", "url", "--jq", ".url")
        if existing.returncode == 0 and existing.stdout.strip():
            return existing.stdout.strip(), None
        created = _gh(repo, "pr", "create", "--head", branch, "--base", target,
                      "--title", title, "--body", body)
    except (subprocess.SubprocessError, OSError) as e:
        return None, f"gh failed: {e}"
    if created.returncode != 0:
        return None, (created.stderr or created.stdout).strip()[-400:]
    lines = created.stdout.strip().splitlines()
    return (lines[-1].strip() if lines else None), None


def merge_pull_request(repo: Path, url: str) -> tuple[bool, str]:
    """Merge the PR with a merge commit; GitHub's own checks and protections apply."""
    try:
        merged = _gh(repo, "pr", "merge", url, "--merge", "--delete-branch")
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"gh failed: {e}"
    if merged.returncode != 0:
        return False, (merged.stderr or merged.stdout).strip()[-400:]
    return True, f"merged {url}"
