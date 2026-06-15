"""API error helpers."""

from __future__ import annotations


class ApiError(Exception):
    """Application error rendered as a JSON {detail, code} response."""

    def __init__(self, code: str, detail: str, status_code: int = 400) -> None:
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)
