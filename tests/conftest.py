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
    return home


@pytest.fixture(autouse=True)
def _no_test_execution(monkeypatch) -> None:
    """Generated tests run in a container by default (FACTORY_RUN_TESTS unset = auto).
    The unit suite must never start one: tests that exercise execution opt in
    explicitly and swap in `tests/verification/sandbox_double.py`."""
    monkeypatch.setenv("FACTORY_RUN_TESTS", "0")
