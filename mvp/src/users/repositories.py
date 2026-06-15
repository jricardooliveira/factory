"""Repository helpers for users and export requests."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from users.models import ExportRequest, User, UserProfile


class UserRepository:
    """Access user data for export generation."""

    def get_user(self, session: Session, user_id: str) -> User | None:
        """Return a user by id."""

        return session.get(User, user_id)

    def get_profile(self, session: Session, user_id: str) -> UserProfile | None:
        """Return a user's profile data."""

        return session.get(UserProfile, user_id)

    def create_export_request(
        self,
        session: Session,
        *,
        export_id: str,
        user_id: str,
        requested_at: datetime,
        expires_at: datetime,
        celery_task_id: str,
        schema_version: int,
    ) -> ExportRequest:
        """Persist a new export request."""

        export_request = ExportRequest(
            id=export_id,
            user_id=user_id,
            status="pending",
            requested_at=requested_at,
            expires_at=expires_at,
            celery_task_id=celery_task_id,
            schema_version=schema_version,
        )
        session.add(export_request)
        session.flush()
        return export_request

    def get_export_request(self, session: Session, export_id: str) -> ExportRequest | None:
        """Return an export request by id."""

        return session.get(ExportRequest, export_id)

    def list_user_exports(self, session: Session, user_id: str) -> list[ExportRequest]:
        """Return all export requests for a user."""

        statement = select(ExportRequest).where(ExportRequest.user_id == user_id)
        return list(session.scalars(statement).all())
