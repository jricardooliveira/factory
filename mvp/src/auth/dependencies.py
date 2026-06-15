"""Authentication helpers for the GDPR export API."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from common.db import get_db
from common.errors import ApiError
from users.models import User


@dataclass(frozen=True)
class CurrentUser:
    """Authenticated user identity."""

    id: str
    email: str


def _extract_user_id(request: Request) -> str | None:
    auth_header = request.headers.get("authorization")
    if auth_header:
        prefix = "bearer "
        if auth_header.lower().startswith(prefix):
            token = auth_header[len(prefix) :].strip()
            if token.startswith("user:"):
                return token.split(":", 1)[1].strip()
            return token or None

    header_user_id = request.headers.get("x-user-id")
    return header_user_id.strip() if header_user_id else None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    """Resolve the current authenticated user from request headers."""

    user_id = _extract_user_id(request)
    if not user_id:
        raise ApiError("AUTH_REQUIRED", "Authentication is required.", status_code=401)

    user = db.get(User, user_id)
    if user is None:
        raise ApiError("AUTH_INVALID", "Authenticated user does not exist.", status_code=401)

    return CurrentUser(id=user.id, email=user.email)
