"""FastAPI application factory for the task board API."""

from __future__ import annotations

from fastapi import FastAPI

from sqlalchemy.orm import sessionmaker

from app.database import build_engine, init_db
from app.routes.tasks import router as tasks_router


def create_app(database_url: str | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(title="Task Board API")
    engine = build_engine(database_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    app.state.engine = engine
    app.state.SessionLocal = session_factory

    init_db(engine)
    app.include_router(tasks_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Return a basic health response."""

        return {"status": "ok"}

    return app


app = create_app()
