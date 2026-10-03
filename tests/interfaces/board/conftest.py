"""UI actions enqueue work, but must never spawn a live model worker from tests."""
import pytest


@pytest.fixture(autouse=True)
def detached_worker_is_not_started(monkeypatch):
    monkeypatch.setattr('factory.interfaces.board.board_app.start_worker', lambda **kwargs: None)
