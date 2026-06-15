"""FastAPI application factory for GDPR personal data exports."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from common.db import build_engine, build_session_factory, init_db
from common.errors import ApiError
from users.routes import router as users_router


def _api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code})


def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and {"detail", "code"}.issubset(detail):
        payload = detail
    else:
        payload = {"detail": str(detail), "code": "HTTP_ERROR"}
    return JSONResponse(status_code=exc.status_code, content=payload)


def _validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": "Validation failed.", "code": "VALIDATION_ERROR"})


def create_app(database_url: str | None = None, storage_dir: str | None = None) -> FastAPI:
    """Create and configure the export API application."""

    app = FastAPI(title="GDPR Personal Data Export API")
    engine = build_engine(database_url)
    session_factory = build_session_factory(engine)

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.storage_dir = Path(storage_dir or os.getenv("EXPORT_STORAGE_DIR", "./export_storage"))
    app.state.storage_dir.mkdir(parents=True, exist_ok=True)

    init_db(engine)

    app.include_router(users_router)

    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Return a basic health response."""

        return {"status": "ok"}

    return app


app = create_app()
