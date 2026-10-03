"""Test configuration: source imports resolve, and no test can reach the real workspace."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture(autouse=True)
def _isolated_factory_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> Path:
    """Point $FACTORY_HOME at a throwaway directory for EVERY test.

    The factory's state (factory.db + every product under projects/) lives in
    $FACTORY_HOME, default ~/.factory. A test that forgot to override it would
    read — or worse, write — the operator's real products. Making the isolation
    autouse means forgetting is impossible rather than merely unlikely.
    """
    home = tmp_path_factory.mktemp("factory-home")
    monkeypatch.setenv("FACTORY_HOME", str(home))
    # The operator's factory.toml (budget cap, timeouts, runner) must not change test
    # outcomes either: point settings at a file that does not exist -> defaults.
    monkeypatch.setenv("FACTORY_SETTINGS", str(home / "factory.toml"))
    for var in ("FACTORY_MAX_STORY_COST_USD", "FACTORY_PROBE_TIMEOUT", "FACTORY_RUNNER"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture(autouse=True)
def _opencode_interview_engine(monkeypatch) -> None:
    """Pin the interview engine to opencode for EVERY test.

    With the optional [claude] extra installed and `claude` on PATH, the default
    "auto" engine calls the Claude Agent SDK — a live, paid call from any test that
    patched only `run_agent`. Tests of the engine choice set the variable themselves.
    """
    monkeypatch.setenv("FACTORY_INTERVIEW_ENGINE", "opencode")
