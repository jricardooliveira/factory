"""Task routes for the task board API."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Task
from app.schemas import (
    TaskCreate,
    TaskListResponse,
    TaskPriority,
    TaskRead,
    TaskStatus,
    TaskUpdate,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])

def _serialize_task(task: Task) -> TaskRead:
    """Convert a SQLAlchemy task into a response schema."""

    return TaskRead.model_validate(task)


def _get_task_or_404(db: Session, task_id: int) -> Task:
    """Fetch a task or raise a 404 error."""

    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


def _priority_rank() -> object:
    """Return a deterministic SQL expression for task priority ordering."""

    return case(
        (Task.priority == TaskPriority.low.value, 1),
        (Task.priority == TaskPriority.medium.value, 2),
        (Task.priority == TaskPriority.high.value, 3),
        else_=4,
    )


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_task(task_in: TaskCreate, db: Session = Depends(get_db)) -> TaskRead:
    """Create a task."""

    task = Task(
        title=task_in.title,
        description=task_in.description,
        status=task_in.status.value,
        priority=task_in.priority.value,
        assigned_to=task_in.assigned_to,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return _serialize_task(task)


@router.get("", response_model=TaskListResponse)
def list_tasks(
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    priority_filter: TaskPriority | None = Query(default=None, alias="priority"),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort_by: Literal["created_at", "priority"] = Query(default="created_at"),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
    db: Session = Depends(get_db),
) -> TaskListResponse:
    """Return tasks with filtering, sorting, and pagination."""

    filters: list[object] = []
    if status_filter is not None:
        filters.append(Task.status == status_filter.value)
    if priority_filter is not None:
        filters.append(Task.priority == priority_filter.value)

    base_query = select(Task).where(*filters)
    total = db.execute(select(func.count()).select_from(Task).where(*filters)).scalar_one()

    if sort_by == "priority":
        order_column = _priority_rank()
    else:
        order_column = Task.created_at

    if sort_order == "desc":
        order_column = order_column.desc()
    else:
        order_column = order_column.asc()

    query = base_query.order_by(order_column, Task.id.asc()).offset(offset).limit(limit)
    tasks: Sequence[Task] = db.execute(query).scalars().all()

    return TaskListResponse(
        items=[_serialize_task(task) for task in tasks],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    """Return a single task."""

    return _serialize_task(_get_task_or_404(db, task_id))


@router.patch("/{task_id}", response_model=TaskRead)
def update_task(task_id: int, task_in: TaskUpdate, db: Session = Depends(get_db)) -> TaskRead:
    """Update the provided task fields."""

    task = _get_task_or_404(db, task_id)
    for field, value in task_in.model_dump(exclude_unset=True).items():
        setattr(task, field, value)

    db.add(task)
    db.commit()
    db.refresh(task)
    return _serialize_task(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: Session = Depends(get_db)) -> Response:
    """Delete a task."""

    task = _get_task_or_404(db, task_id)
    db.delete(task)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
