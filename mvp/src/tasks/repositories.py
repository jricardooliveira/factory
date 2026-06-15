"""Repository helpers for task export data."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from tasks.models import Task


class TaskRepository:
    """Access user-owned tasks."""

    def list_for_user(self, session: Session, user_id: str) -> list[Task]:
        """Return all tasks belonging to a single user."""

        statement = select(Task).where(Task.user_id == user_id).order_by(Task.created_at.asc())
        return list(session.scalars(statement).all())
