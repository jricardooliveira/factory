"""Pydantic schemas for the task board API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(str, Enum):
    """Allowed task statuses."""

    todo = "todo"
    in_progress = "in_progress"
    done = "done"


class TaskPriority(str, Enum):
    """Allowed task priorities."""

    low = "low"
    medium = "medium"
    high = "high"


class TaskCreate(BaseModel):
    """Schema for creating a task."""

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    status: TaskStatus = TaskStatus.todo
    priority: TaskPriority = TaskPriority.medium
    assigned_to: str | None = Field(default=None, max_length=200)


class TaskUpdate(BaseModel):
    """Schema for updating a task."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    assigned_to: str | None = Field(default=None, max_length=200)


class TaskRead(BaseModel):
    """Schema for returning a task."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    assigned_to: str | None
    created_at: datetime


class TaskListResponse(BaseModel):
    """Schema for paginated task listings."""

    items: list[TaskRead]
    total: int
    limit: int
    offset: int
