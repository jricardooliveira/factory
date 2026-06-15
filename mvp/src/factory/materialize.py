"""Write coder-agent code blocks into a project repository."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def normalize_block_path(root: Path, relative: str) -> str:
    """Strip a leading 'repo/' when the root dir is itself named 'repo'.

    Agents emit repo-rooted paths (e.g. 'repo/app/main.py') while the working
    directory already IS the repo, which would nest files in 'repo/repo/...'.
    Idempotent; only triggers for that doubled-repo case.
    """
    if root.name == "repo":
        for prefix in ("repo/", "./repo/"):
            if relative.startswith(prefix):
                return relative[len(prefix):]
    return relative


def _safe_target(root: Path, relative_path: str) -> Path:
    if not relative_path:
        raise ValueError("Code block path cannot be empty")

    root_resolved = root.resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Code block path is outside project root: {relative_path}") from exc
    return target


def _block_value(block: Any, key: str, default: str = "") -> str:
    if isinstance(block, dict):
        return str(block.get(key, default))
    return str(getattr(block, key, default))


def materialize_code_blocks(code_blocks: list[Any], *, root: Path) -> list[Path]:
    """Write create/modify code blocks under root and return written paths."""

    written: list[Path] = []
    for block in code_blocks:
        action = _block_value(block, "action", "create")
        if action not in {"create", "modify"}:
            raise ValueError(f"Unsupported code block action: {action}")

        relative_path = _block_value(block, "path")
        target = _safe_target(root, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_block_value(block, "content"), encoding="utf-8")
        written.append(target)

    return written
