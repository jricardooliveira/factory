"""Tests for the health check endpoint."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


class HealthEndpointTests(unittest.TestCase):
    """Health endpoint behavior."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "tasks.db"
        self.client = TestClient(create_app(f"sqlite:///{self.db_path}"))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_health_returns_ok(self) -> None:
        """It returns a simple OK payload."""

        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
