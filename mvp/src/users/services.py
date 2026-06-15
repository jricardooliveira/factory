"""Business logic for personal data exports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from common.errors import ApiError
from tasks.repositories import TaskRepository
from users.models import ExportRequest
from users.repositories import UserRepository
from users.schemas import ExportRequestRead
from jobs.export_jobs import generate_export_archive

EXPORT_SCHEMA_VERSION = 1
EXPORT_RETENTION_DAYS = 30


def _normalize_datetime(value: datetime) -> datetime:
    """Convert a datetime to a naive UTC value for SQLite-friendly comparisons."""

    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _serialize_export_request(export_request: ExportRequest) -> ExportRequestRead:
    """Convert an export request model into its public schema."""

    return ExportRequestRead.model_validate(export_request, from_attributes=True)


def _load_export_request_or_404(
    session: Session, user_id: str, export_id: str, user_repo: UserRepository
) -> ExportRequest:
    export_request = user_repo.get_export_request(session, export_id)
    if export_request is None:
        raise ApiError("EXPORT_NOT_FOUND", "Export request was not found.", status_code=404)
    if export_request.user_id != user_id:
        raise ApiError("EXPORT_FORBIDDEN", "You may only access your own exports.", status_code=403)
    return export_request


def request_personal_data_export(
    session_factory: sessionmaker[Session],
    user_id: str,
    storage_dir: str | Path,
    now: datetime | None = None,
) -> ExportRequestRead:
    """Create a new export request and generate the ZIP artifact."""

    current_time = now or datetime.now(timezone.utc)
    user_repo = UserRepository()
    export_id = str(uuid4())
    celery_task_id = export_id

    with session_factory() as session:
        user = user_repo.get_user(session, user_id)
        if user is None:
            raise ApiError("AUTH_INVALID", "Authenticated user does not exist.", status_code=401)

        user_repo.create_export_request(
            session,
            export_id=export_id,
            user_id=user_id,
            requested_at=current_time,
            expires_at=current_time + timedelta(days=EXPORT_RETENTION_DAYS),
            celery_task_id=celery_task_id,
            schema_version=EXPORT_SCHEMA_VERSION,
        )
        session.commit()

    generate_export_archive(session_factory, export_id=export_id, storage_dir=storage_dir, now=current_time)

    with session_factory() as session:
        export_request = user_repo.get_export_request(session, export_id)
        if export_request is None:
            raise ApiError("EXPORT_NOT_FOUND", "Export request was not found.", status_code=404)
        return _serialize_export_request(export_request)


def get_export_status(
    session_factory: sessionmaker[Session],
    user_id: str,
    export_id: str,
) -> ExportRequestRead:
    """Return the current status for an export request."""

    user_repo = UserRepository()
    with session_factory() as session:
        export_request = _load_export_request_or_404(session, user_id, export_id, user_repo)
        return _serialize_export_request(export_request)


def get_export_download_path(
    session_factory: sessionmaker[Session],
    user_id: str,
    export_id: str,
    storage_dir: str | Path,
    now: datetime | None = None,
) -> Path:
    """Resolve a ready export to a ZIP file path after ownership and expiry checks."""

    current_time = now or datetime.now(timezone.utc)
    user_repo = UserRepository()
    with session_factory() as session:
        export_request = _load_export_request_or_404(session, user_id, export_id, user_repo)

        if _normalize_datetime(current_time) >= _normalize_datetime(export_request.expires_at):
            export_request.status = "expired"
            export_request.error_detail = "Export artifact expired after the retention window."
            if export_request.file_path:
                Path(export_request.file_path).unlink(missing_ok=True)
            session.commit()
            raise ApiError("EXPORT_EXPIRED", "The export has expired.", status_code=410)

        if export_request.status != "ready" or not export_request.file_path:
            raise ApiError("EXPORT_NOT_READY", "The export is not ready yet.", status_code=409)

        file_path = Path(export_request.file_path)
        if not file_path.exists():
            raise ApiError("EXPORT_NOT_FOUND", "Export file is unavailable.", status_code=404)

        return file_path


def expire_export_request(
    session_factory: sessionmaker[Session],
    export_id: str,
    now: datetime | None = None,
) -> None:
    """Mark a request as expired if its retention window has elapsed."""

    current_time = now or datetime.now(timezone.utc)
    user_repo = UserRepository()
    with session_factory() as session:
        export_request = user_repo.get_export_request(session, export_id)
        if export_request is None:
            raise ApiError("EXPORT_NOT_FOUND", "Export request was not found.", status_code=404)
        if _normalize_datetime(current_time) < _normalize_datetime(export_request.expires_at):
            return

        export_request.status = "expired"
        export_request.completed_at = export_request.completed_at or current_time
        export_request.error_detail = "Export artifact expired after the retention window."
        if export_request.file_path:
            Path(export_request.file_path).unlink(missing_ok=True)
            export_request.file_path = None
            export_request.file_size = None
            export_request.checksum = None
        session.commit()
