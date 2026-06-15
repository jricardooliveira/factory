"""Database utilities for the task board API."""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""


def build_database_url(database_url: str | None = None) -> str:
    """Return the configured database URL."""

    return database_url or os.getenv("DATABASE_URL", "sqlite:///./task_board.db")


def build_engine(database_url: str | None = None) -> Any:
    """Create a SQLAlchemy engine for the configured database."""

    url = build_database_url(database_url)
    connect_args: dict[str, Any] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_engine(url, connect_args=connect_args)


def build_session_factory(engine: Any) -> sessionmaker[Session]:
    """Create a session factory bound to an engine."""

    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db(engine: Any) -> None:
    """Create all database tables."""

    from app.models import Task  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session tied to the current application."""

    session_factory = request.app.state.SessionLocal
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
