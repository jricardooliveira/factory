"""Export generation and retention jobs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from common.errors import ApiError
from tasks.repositories import TaskRepository
from users.models import ExportRequest
from users.repositories import UserRepository


def _normalize_datetime(value: datetime) -> datetime:
    """Convert a datetime to a naive UTC value for SQLite-friendly comparisons."""

    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported value type: {type(value)!r}")


def _write_json_to_zip(archive: zipfile.ZipFile, path: str, payload: Any) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default)
    archive.writestr(path, data.encode("utf-8"))


def _serialize_user_profile(user: Any, profile: Any) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "created_at": user.created_at,
        "profile": None
        if profile is None
        else {
            "display_name": profile.display_name,
            "locale": profile.locale,
            "phone_number": profile.phone_number,
            "address_line1": profile.address_line1,
            "city": profile.city,
            "country": profile.country,
            "notes": profile.notes,
        },
    }


def _serialize_tasks(tasks: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "status": task.status,
            "priority": task.priority,
            "created_at": task.created_at,
        }
        for task in tasks
    ]


def generate_export_archive(
    session_factory: sessionmaker[Session],
    *,
    export_id: str,
    storage_dir: str | Path,
    now: datetime | None = None,
) -> ExportRequest:
    """Build the ZIP archive for a personal data export request."""

    current_time = now or datetime.now(timezone.utc)
    storage_root = Path(storage_dir)
    export_root = storage_root / "exports"
    export_root.mkdir(parents=True, exist_ok=True)

    user_repo = UserRepository()
    task_repo = TaskRepository()

    with session_factory() as session:
        export_request = user_repo.get_export_request(session, export_id)
        if export_request is None:
            raise ApiError("EXPORT_NOT_FOUND", "Export request was not found.", status_code=404)

        final_path = export_root / f"{export_id}.zip"
        if export_request.status == "ready" and final_path.exists():
            return export_request

        export_request.status = "processing"
        export_request.celery_task_id = export_request.celery_task_id or export_id
        session.commit()

        user = user_repo.get_user(session, export_request.user_id)
        if user is None:
            export_request.status = "failed"
            export_request.error_detail = "Export user no longer exists."
            session.commit()
            raise ApiError("EXPORT_USER_MISSING", "The export user no longer exists.", status_code=404)

        profile = user_repo.get_profile(session, export_request.user_id)
        tasks = task_repo.list_for_user(session, export_request.user_id)

    manifest = {
        "schema_version": export_request.schema_version,
        "export_id": export_id,
        "user_id": export_request.user_id,
        "requested_at": export_request.requested_at,
        "generated_at": current_time,
        "expires_at": export_request.expires_at,
        "files": [
            {"path": "manifest.json", "content_type": "application/json"},
            {"path": "profile.json", "content_type": "application/json"},
            {"path": "tasks.json", "content_type": "application/json"},
        ],
    }

    with tempfile.NamedTemporaryFile(dir=export_root, suffix=".tmp", delete=False) as temp_file:
        temp_path = Path(temp_file.name)

    try:
        with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write_json_to_zip(archive, "manifest.json", manifest)
            _write_json_to_zip(archive, "profile.json", _serialize_user_profile(user, profile))
            _write_json_to_zip(archive, "tasks.json", _serialize_tasks(tasks))

        checksum = hashlib.sha256()
        file_size = 0
        with temp_path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                file_size += len(chunk)
                checksum.update(chunk)

        os.replace(temp_path, final_path)

        with session_factory() as session:
            export_request = user_repo.get_export_request(session, export_id)
            if export_request is None:
                raise ApiError("EXPORT_NOT_FOUND", "Export request was not found.", status_code=404)

            export_request.status = "ready"
            export_request.completed_at = current_time
            export_request.file_path = str(final_path)
            export_request.file_size = file_size
            export_request.checksum = checksum.hexdigest()
            export_request.error_detail = None
            session.commit()
            return export_request
    except Exception as exc:  # pragma: no cover - defensive cleanup
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        with session_factory() as session:
            export_request = user_repo.get_export_request(session, export_id)
            if export_request is not None:
                export_request.status = "failed"
                export_request.error_detail = str(exc)
                session.commit()
        raise


def cleanup_expired_exports(
    session_factory: sessionmaker[Session],
    *,
    now: datetime | None = None,
) -> int:
    """Expire and delete all exports whose retention period has elapsed."""

    current_time = now or datetime.now(timezone.utc)
    user_repo = UserRepository()
    removed = 0

    with session_factory() as session:
        statements = []
        exports = session.query(ExportRequest).all()
        for export_request in exports:
            if (
                _normalize_datetime(export_request.expires_at) > _normalize_datetime(current_time)
                or export_request.status == "expired"
            ):
                continue

            export_request.status = "expired"
            export_request.completed_at = export_request.completed_at or current_time
            export_request.error_detail = "Export artifact expired after the retention window."
            if export_request.file_path:
                Path(export_request.file_path).unlink(missing_ok=True)
                export_request.file_path = None
                export_request.file_size = None
                export_request.checksum = None
            removed += 1

        if removed:
            session.commit()

    return removed
