"""A stand-in for `verification.sandbox.run_in_container` that needs no container runtime.

It keeps the property that matters for behaviour tests — the command runs against a
COPY of the repository, never the repository itself — and runs it on the host. Only
tests use it; the factory itself never executes generated code outside a container.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from factory.verification.sandbox import SandboxResult


def host_execute(repo: Path, toolchain: str, workdir: str, argv: list[str], *,
                 timeout: int) -> SandboxResult:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "work"
        shutil.copytree(repo, copy, ignore=shutil.ignore_patterns(".git"))
        if argv[:1] == ["python"]:
            argv = [sys.executable, *argv[1:]]
        try:
            proc = subprocess.run(argv, cwd=copy / workdir, capture_output=True, text=True,
                                  timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return SandboxResult(None, "timed out")
        return SandboxResult(proc.returncode, (proc.stdout or "") + (proc.stderr or ""))
