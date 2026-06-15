"""Integration tests for the task board API."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


class TaskApiTests(unittest.TestCase):
    """Behavioral tests for CRUD, filtering, sorting, and validation."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "tasks.db"
        self.client = TestClient(create_app(f"sqlite:///{self.db_path}"))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _create_task(self, **overrides: object) -> dict[str, object]:
        payload = {
            "title": "Write tests",
            "description": "Cover the API behavior",
            "status": "todo",
            "priority": "medium",
            "assigned_to": "Ada",
        }
        payload.update(overrides)

        response = self.client.post("/tasks", json=payload)

        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_crud_flow_persists_task_data(self) -> None:
        """It supports create, get, update, and delete for a task."""

        created = self._create_task()
        self.assertIn("id", created)
        self.assertIn("created_at", created)
        self.assertEqual(created["title"], "Write tests")

        task_id = created["id"]
        fetched = self.client.get(f"/tasks/{task_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["title"], "Write tests")

        updated = self.client.patch(
            f"/tasks/{task_id}",
            json={"status": "in_progress", "priority": "high", "assigned_to": "Grace"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["status"], "in_progress")
        self.assertEqual(updated.json()["priority"], "high")
        self.assertEqual(updated.json()["assigned_to"], "Grace")
        self.assertEqual(updated.json()["created_at"], created["created_at"])

        deleted = self.client.delete(f"/tasks/{task_id}")
        self.assertEqual(deleted.status_code, 204)

        missing = self.client.get(f"/tasks/{task_id}")
        self.assertEqual(missing.status_code, 404)

    def test_list_supports_filtering_pagination_and_created_at_sorting(self) -> None:
        """It filters by status and priority and paginates ordered results."""

        self._create_task(title="First", priority="low", status="todo")
        second = self._create_task(title="Second", priority="high", status="todo")
        self._create_task(title="Third", priority="high", status="done")

        filtered = self.client.get("/tasks", params={"status": "todo", "priority": "high"})
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual([task["title"] for task in filtered.json()["items"]], ["Second"])

        paginated = self.client.get(
            "/tasks",
            params={"sort_by": "created_at", "sort_order": "asc", "limit": 1, "offset": 1},
        )
        self.assertEqual(paginated.status_code, 200)
        self.assertEqual(paginated.json()["items"][0]["title"], second["title"])
        self.assertEqual(paginated.json()["limit"], 1)
        self.assertEqual(paginated.json()["offset"], 1)
        self.assertEqual(paginated.json()["total"], 3)

    def test_list_sorts_by_priority_in_custom_order(self) -> None:
        """It sorts priority using low, medium, high ordering."""

        self._create_task(title="Low", priority="low")
        self._create_task(title="High", priority="high")
        self._create_task(title="Medium", priority="medium")

        ascending = self.client.get("/tasks", params={"sort_by": "priority", "sort_order": "asc"})
        self.assertEqual([task["title"] for task in ascending.json()["items"]], ["Low", "Medium", "High"])

        descending = self.client.get("/tasks", params={"sort_by": "priority", "sort_order": "desc"})
        self.assertEqual([task["title"] for task in descending.json()["items"]], ["High", "Medium", "Low"])

    def test_missing_and_invalid_inputs_return_expected_status_codes(self) -> None:
        """It returns 404 for missing tasks and 422 for invalid payloads."""

        self.assertEqual(self.client.get("/tasks/9999").status_code, 404)
        self.assertEqual(self.client.patch("/tasks/9999", json={"title": "Nope"}).status_code, 404)
        self.assertEqual(self.client.delete("/tasks/9999").status_code, 404)

        invalid = self.client.post(
            "/tasks",
            json={
                "title": "Bad task",
                "description": "",
                "status": "invalid",
                "priority": "urgent",
                "assigned_to": "Ada",
            },
        )
        self.assertEqual(invalid.status_code, 422)
