"""SQLAlchemy models for the task board API."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Task(Base):
    """Persistent task record."""

    __tablename__ = "tasks"

    __table_args__ = (
        CheckConstraint("status IN ('todo', 'in_progress', 'done')", name="task_status_check"),
        CheckConstraint("priority IN ('low', 'medium', 'high')", name="task_priority_check"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="todo")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    assigned_to: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
