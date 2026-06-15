"""User and export request models."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from common.db import Base


class User(Base):
    """Application user record."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    profile: Mapped["UserProfile"] = relationship(back_populates="user", uselist=False)


class UserProfile(Base):
    """Additional personal data exported with the account profile."""

    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    locale: Mapped[str] = mapped_column(String(32), nullable=False, default="en-US")
    phone_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="profile")


class ExportRequest(Base):
    """Personal data export request and generated artifact metadata."""

    __tablename__ = "export_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_size: Mapped[int | None] = mapped_column(nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schema_version: Mapped[int] = mapped_column(nullable=False, default=1)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
