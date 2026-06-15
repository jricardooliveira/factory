"""Tests for the GDPR personal data export flow."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from main import create_app
from jobs.export_jobs import cleanup_expired_exports
from tasks.models import Task
from users.models import ExportRequest, User, UserProfile


def _seed_user(session_factory, *, user_id: str, email: str) -> None:
    with session_factory() as session:
        session.add(User(id=user_id, email=email, full_name="Jane Doe"))
        session.add(
            UserProfile(
                user_id=user_id,
                display_name="Jane",
                locale="en-GB",
                phone_number="+44 20 0000 0000",
                address_line1="1 Export Street",
                city="London",
                country="GB",
                notes="GDPR export test profile",
            )
        )
        session.add(
            Task(
                id=str(uuid4()),
                user_id=user_id,
                title="Personal task",
                description="User-owned task data",
                status="done",
                priority="high",
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()


def _make_client(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'db.sqlite3'}"
    storage_dir = tmp_path / "exports"
    app = create_app(database_url=database_url, storage_dir=str(storage_dir))
    return TestClient(app), app


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer user:{user_id}"}


def test_user_can_request_and_download_own_export(tmp_path: Path) -> None:
    """A user can create an export request and download the ZIP artifact."""

    client, app = _make_client(tmp_path)
    user_id = str(uuid4())
    _seed_user(app.state.session_factory, user_id=user_id, email="jane@example.com")

    response = client.post("/users/me/exports", headers=_auth_headers(user_id))
    assert response.status_code == 202

    export_data = response.json()
    assert export_data["status"] == "ready"
    assert export_data["schema_version"] == 1
    assert export_data["checksum"]

    download = client.get(f"/users/me/exports/{export_data['id']}/download", headers=_auth_headers(user_id))
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/zip")

    archive = zipfile.ZipFile(io.BytesIO(download.content))
    assert set(archive.namelist()) == {"manifest.json", "profile.json", "tasks.json"}

    manifest = archive.read("manifest.json").decode("utf-8")
    profile = archive.read("profile.json").decode("utf-8")
    tasks = archive.read("tasks.json").decode("utf-8")

    assert '"export_id":' in manifest
    assert '"display_name": "Jane"' in profile
    assert '"title": "Personal task"' in tasks


def test_only_owner_can_access_export(tmp_path: Path) -> None:
    """A different authenticated user cannot access another user's export."""

    client, app = _make_client(tmp_path)
    owner_id = str(uuid4())
    other_user_id = str(uuid4())
    _seed_user(app.state.session_factory, user_id=owner_id, email="owner@example.com")
    _seed_user(app.state.session_factory, user_id=other_user_id, email="other@example.com")

    create_response = client.post("/users/me/exports", headers=_auth_headers(owner_id))
    export_id = create_response.json()["id"]

    forbidden = client.get(f"/users/me/exports/{export_id}", headers=_auth_headers(other_user_id))
    assert forbidden.status_code == 403
    assert forbidden.json() == {
        "detail": "You may only access your own exports.",
        "code": "EXPORT_FORBIDDEN",
    }


def test_expired_exports_are_cleaned_up(tmp_path: Path) -> None:
    """Expired exports are marked expired and their ZIP files are removed."""

    client, app = _make_client(tmp_path)
    user_id = str(uuid4())
    _seed_user(app.state.session_factory, user_id=user_id, email="jane@example.com")

    create_response = client.post("/users/me/exports", headers=_auth_headers(user_id))
    export_id = create_response.json()["id"]

    with app.state.session_factory() as session:
        export_request = session.get(ExportRequest, export_id)
        assert export_request is not None
        export_request.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        session.commit()

    removed = cleanup_expired_exports(app.state.session_factory, now=datetime.now(timezone.utc))
    assert removed == 1

    with app.state.session_factory() as session:
        export_request = session.get(ExportRequest, export_id)
        assert export_request is not None
        assert export_request.status == "expired"
        assert export_request.file_path is None

    download = client.get(f"/users/me/exports/{export_id}/download", headers=_auth_headers(user_id))
    assert download.status_code == 410
    assert download.json() == {"detail": "The export has expired.", "code": "EXPORT_EXPIRED"}
