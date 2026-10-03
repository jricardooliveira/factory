"""Shared verification primitives: the check/result types, the subprocess runner,
the timeouts and the opt-in switch for executing tests.

Lives apart from the package ``__init__`` so the toolchain modules (python, go,
typescript) can import it without a cycle back through the package that imports them.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from factory.agent_config.settings import settings

# Timeouts come from factory.toml [timeouts]. A build (`go build` resolving modules on a
# cold cache, `tsc -p` over a whole project) gets more room than the py_compile default.


def command_timeout() -> int:
    return settings().timeouts.command


def suite_timeout() -> int:
    return settings().timeouts.test


def build_timeout() -> int:
    return settings().timeouts.build
# How much of one failed check's output reaches the gate reason (and so the coder's
# retry, the run error and `factory review`): enough for a traceback's last frame.
MAX_FAILURE_DETAIL = 3500


def tests_enabled() -> bool:
    """Whether to actually RUN materialized tests (opt-in).

    Off by default: executing agent-generated code is risky without true OS-level
    isolation. Operators who trust their setup opt in with FACTORY_RUN_TESTS=1 or
    ``run_tests = true`` in factory.toml.
    """
    return settings().features.run_tests


@dataclass
class VerifyCheck:
    name: str
    status: str  # "pass" | "fail" | "warn" | "skip"
    detail: str = ""
    # Test files (repo-relative) this check saw fail: what a retry may also change.
    files: tuple[str, ...] = ()


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

    @property
    def failing_test_files(self) -> list[str]:
        return sorted({f for c in self.checks if c.status == "fail" for f in c.files})

    @property
    def failures(self) -> list[str]:
        """One `name: detail` per failed check — the WHY the summary leaves out."""
        return [f"{c.name}: {c.detail.strip()[:MAX_FAILURE_DETAIL]}"
                for c in self.checks if c.status == "fail"]


def run_command(
    cmd: list[str], cwd: Path, timeout: int | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True,
        timeout=command_timeout() if timeout is None else timeout,
        # Never inherit stdin: agent-written code under test (`go test`, pytest)
        # reading it would block the gate. See adapters.opencode.run_agent.
        stdin=subprocess.DEVNULL,
    )
