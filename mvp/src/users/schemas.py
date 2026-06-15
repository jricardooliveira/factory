"""Pydantic schemas for export responses."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


ExportStatus = Literal["pending", "processing", "ready", "failed", "expired"]


class ExportRequestRead(BaseModel):
    """Public export request representation."""

    model_config = {"from_attributes": True}

    id: str
    status: ExportStatus
    requested_at: datetime
    completed_at: datetime | None
    expires_at: datetime
    file_size: int | None
    checksum: str | None
    schema_version: int
    error_detail: str | None = None
