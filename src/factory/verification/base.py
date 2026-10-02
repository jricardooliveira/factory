"""Shared verification primitives: the check/result types, the subprocess runner,
the timeouts and the opt-in switch for executing tests.

Lives apart from the package ``__init__`` so the toolchain modules (python, go,
typescript) can import it without a cycle back through the package that imports them.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

COMMAND_TIMEOUT = 60
TEST_TIMEOUT = 180
# A build (`go build` resolving modules on a cold cache, `tsc -p` over a whole
# project) gets more room than the 60s a py_compile needs.
BUILD_TIMEOUT = 180


def test_mode() -> str:
    """`FACTORY_RUN_TESTS`: unset = "auto", 0/false/no/off = "off", 1/true/yes/on = "on".

    Generated tests only ever run inside a container (`verification.sandbox`).
    "auto" runs them when a container runtime is up and reports them as not run
    otherwise; "on" FAILS the check without a runtime rather than run on the host.
    """
    value = os.environ.get("FACTORY_RUN_TESTS", "").strip().lower()
    if value in ("0", "false", "no", "off"):
        return "off"
    if value in ("1", "true", "yes", "on"):
        return "on"
    return "auto"


def tests_enabled() -> bool:
    """Whether test execution is wanted at all (it then happens in a container)."""
    return test_mode() != "off"


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


def run_command(
    cmd: list[str], cwd: Path, timeout: int = COMMAND_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        # Never inherit stdin: agent-written code under test (`go test`, pytest)
        # reading it would block the gate. See adapters.opencode.run_agent.
        stdin=subprocess.DEVNULL,
    )
