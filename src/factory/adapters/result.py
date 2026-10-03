"""What every agent runner returns, and the helpers the runners share."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


def _default_timeout() -> int:
    """Per-agent subprocess timeout in seconds (override via FACTORY_AGENT_TIMEOUT)."""
    try:
        return max(1, int(os.environ.get("FACTORY_AGENT_TIMEOUT", "600")))
    except ValueError:
        return 600


@dataclass
class AgentResult:
    agent: str
    output: str
    duration_secs: float
    returncode: int
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    model_name: str | None = None
    agent_prompt_hash: str | None = None

    @property
    def success(self) -> bool:
        return self.returncode == 0


def _hash_agent_definition(agent_name: str, cwd: str | None) -> str | None:
    """Hash the .opencode/agents/<name>.md that opencode would resolve from cwd.

    Walks up the directory tree from cwd (mirroring opencode's config discovery)
    and hashes the first matching agent definition. Returns None if not found.
    """
    start = Path(cwd) if cwd else Path.cwd()
    try:
        start = start.resolve()
    except OSError:
        return None
    for directory in [start, *start.parents]:
        candidate = directory / ".opencode" / "agents" / f"{agent_name}.md"
        if candidate.is_file():
            try:
                data = candidate.read_bytes()
            except OSError:
                return None
            return hashlib.sha256(data).hexdigest()[:16]
    return None
