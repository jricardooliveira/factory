"""Run generated tests in a disposable container — never on the host.

Operator decision (2026-10-02, review task T03): a container. Each run gets a fresh
container from the toolchain's image; the repository is COPIED in (`docker cp`), so
no host directory is mounted and nothing the tests write can reach the host. No host
environment variable or credential is passed (HOME points at /tmp), all Linux
capabilities are dropped, CPU / memory / process counts are limited, and a hard
timeout kills the container. It is always removed afterwards.

The network stays on so dependencies can be installed (pip, go modules); nothing
secret is inside to leak. There are deliberately no shared cache volumes: one run's
tests could otherwise poison the next run's dependencies.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from functools import cache
from pathlib import Path

RUNTIMES = ("docker", "podman")

IMAGES = {
    "python": "python:3.12-slim",
    "go": "golang:1",
}

LIMITS = [
    "--cpus", "2", "--memory", "2g", "--pids-limit", "512",
    "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
]

NO_RUNTIME = (
    "no container runtime (docker/podman) is running, so the tests were not run — "
    "generated code is never executed on the host. Start Docker, or set "
    "FACTORY_RUN_TESTS=0 to stop asking."
)

# Per toolchain: what runs before the test command, inside /work.
_SETUP = {
    "python": (
        "python -m pip install -q --disable-pip-version-check --root-user-action=ignore "
        "pytest >/dev/null 2>&1 || echo 'FACTORY: pytest could not be installed'\n"
        "if [ -f requirements.txt ]; then python -m pip install -q "
        "--disable-pip-version-check --root-user-action=ignore -r requirements.txt "
        ">/dev/null 2>&1 || echo 'FACTORY: requirements.txt could not be installed'; fi\n"
    ),
    "go": "",
}
_ENV = {
    "python": {},
    # -mod=mod lets the copy update go.sum (a missing entry is not the code's fault).
    "go": {"GOFLAGS": "-mod=mod", "GOTOOLCHAIN": "auto"},
}
_CONTROL_TIMEOUT = 120  # create / cp / rm
_PULL_TIMEOUT = 900  # a first pull of golang:1 is large


@dataclass(frozen=True)
class SandboxResult:
    returncode: int | None  # None = the test command timed out and was killed
    output: str
    error: str | None = None  # the sandbox itself failed (pull, create, copy)


def _run(argv: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    # Never inherit stdin: agent-written code reading it would block the gate.
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


@cache
def container_runtime() -> str | None:
    """The first runtime whose daemon answers, or None."""
    for runtime in RUNTIMES:
        if shutil.which(runtime) is None:
            continue
        try:
            if _run([runtime, "info"], timeout=15).returncode == 0:
                return runtime
        except (subprocess.SubprocessError, OSError):
            continue
    return None


def _ensure_image(runtime: str, image: str) -> str | None:
    """Pull `image` if it is not local; an error message, or None."""
    try:
        if _run([runtime, "image", "inspect", image], timeout=_CONTROL_TIMEOUT).returncode == 0:
            return None
        pulled = _run([runtime, "pull", image], timeout=_PULL_TIMEOUT)
    except (subprocess.SubprocessError, OSError) as e:
        return f"could not pull {image}: {e}"
    return None if pulled.returncode == 0 else f"could not pull {image}: {pulled.stderr[-300:]}"


def run_in_container(repo: Path, toolchain: str, workdir: str, argv: list[str], *,
            timeout: int) -> SandboxResult:
    """Run `argv` in `<copy of repo>/<workdir>` inside a fresh container."""
    runtime = container_runtime()
    if runtime is None:
        return SandboxResult(None, "", error=NO_RUNTIME)
    image = IMAGES[toolchain]
    problem = _ensure_image(runtime, image)
    if problem:
        return SandboxResult(None, "", error=problem)

    target = "/work" if workdir in ("", ".") else f"/work/{workdir}"
    # `docker cp` keeps the host owner; with every capability dropped, root could not
    # write into it. Re-copying inside gives the tests a /work the container owns.
    script = (
        "cp -R /src /work && cd /work\n"
        f"{_SETUP[toolchain]}cd {shlex.quote(target)} && exec {shlex.join(argv)}\n"
    )
    env = [arg for k, v in {"HOME": "/tmp", **_ENV[toolchain]}.items() for arg in ("-e", f"{k}={v}")]
    name = f"factory-test-{uuid.uuid4().hex[:12]}"
    try:
        created = _run([runtime, "create", "--name", name, *LIMITS, *env, image,
                        "sh", "-c", script], timeout=_CONTROL_TIMEOUT)
        if created.returncode != 0:
            return SandboxResult(None, "", error=f"could not create the container: "
                                                 f"{created.stderr[-300:]}")
        copied = _run([runtime, "cp", f"{repo}/.", f"{name}:/src"], timeout=_CONTROL_TIMEOUT)
        if copied.returncode != 0:
            return SandboxResult(None, "", error=f"could not copy the repository in: "
                                                 f"{(copied.stderr or copied.stdout)[-300:]}")
        try:
            done = _run([runtime, "start", "-a", name], timeout=timeout)
        except subprocess.TimeoutExpired:
            _run([runtime, "kill", name], timeout=_CONTROL_TIMEOUT)
            return SandboxResult(None, f"timed out after {timeout}s")
        return SandboxResult(done.returncode, (done.stdout or "") + (done.stderr or ""))
    except (subprocess.SubprocessError, OSError) as e:
        return SandboxResult(None, "", error=f"the container runtime failed: {e}")
    finally:
        try:
            _run([runtime, "rm", "-f", name], timeout=_CONTROL_TIMEOUT)
        except (subprocess.SubprocessError, OSError):
            pass
