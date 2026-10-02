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


# Paths an agent must never write, even INSIDE the repo root. `_safe_target` only
# stops traversal *outside* the root; nothing stopped a coder emitting `.env` or
# `.git/config`. This is the playbook's "keep credentials out of diffs" guardrail,
# placed at the factory's single write chokepoint rather than in an advisory hook —
# the agents have no tools, so every write passes through here.
_FORBIDDEN_DIRS = frozenset({".git", ".secrets", ".ssh", ".aws", ".gnupg", ".config"})
_FORBIDDEN_NAMES = frozenset({
    ".env", ".envrc", ".netrc", ".npmrc", ".pypirc", ".htpasswd",
    "credentials", "id_rsa", "id_ed25519", "id_dsa", "id_ecdsa",
})
_FORBIDDEN_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")


def _reject_sensitive(relative_path: str) -> None:
    """Raise if a path is credential-shaped or points into VCS/secret machinery."""
    parts = [p for p in Path(relative_path).parts if p not in (".", "")]
    for part in parts[:-1] if len(parts) > 1 else []:
        if part in _FORBIDDEN_DIRS:
            raise ValueError(
                f"Refusing to materialize into '{part}/': {relative_path}. Agents may "
                f"not write VCS or secret-store paths — a write here could repoint the "
                f"git baseline the trust package measures the diff against."
            )
    name = parts[-1] if parts else relative_path
    if name in _FORBIDDEN_DIRS or name in _FORBIDDEN_NAMES:
        raise ValueError(
            f"Refusing to materialize credential-shaped path: {relative_path}. "
            f"Secrets must never enter a factory-authored diff."
        )
    if name.startswith(".env"):
        raise ValueError(
            f"Refusing to materialize credential-shaped path: {relative_path} "
            f"(.env* holds secrets and must not enter a factory-authored diff)."
        )
    if name.lower().endswith(_FORBIDDEN_SUFFIXES):
        raise ValueError(
            f"Refusing to materialize key material: {relative_path}."
        )


def _safe_target(root: Path, relative_path: str) -> Path:
    if not relative_path:
        raise ValueError("Code block path cannot be empty")

    _reject_sensitive(relative_path)

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
    """Write create/modify code blocks under root and return written paths.

    ALL-OR-NOTHING: every block is validated before any file is written. Writing
    as it went meant an invalid third block left the first two on disk, and the
    scope check then reported those two as undeclared out-of-band writes —
    blaming the agent for the orchestrator's partial failure.
    """
    planned: list[tuple[Path, str]] = []
    for block in code_blocks:
        action = _block_value(block, "action", "create")
        if action not in {"create", "modify"}:
            raise ValueError(f"Unsupported code block action: {action}")
        target = _safe_target(root, _block_value(block, "path"))
        planned.append((target, _block_value(block, "content")))

    written: list[Path] = []
    for target, content in planned:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)

    return written
