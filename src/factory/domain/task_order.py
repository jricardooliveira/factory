"""Dependency order of a story's tasks (pure, no I/O).

Shared by the pipeline (which executes tasks in this order) and the artifact
chain (PLAN.md records it), so it lives below both rather than inside either.
"""

from __future__ import annotations

from factory.domain.contracts import TaskDef


def order_tasks(tasks: list[TaskDef]) -> list[TaskDef]:
    """Return tasks in dependency order (Kahn topological sort).

    Robust to bad data: dependencies on unknown ids are ignored; if a cycle
    remains, the still-blocked tasks are appended in their original order rather
    than dropped.
    """
    by_id = {t.id: t for t in tasks}
    indegree: dict[str, int] = {t.id: 0 for t in tasks}
    dependents: dict[str, list[str]] = {t.id: [] for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            if dep in by_id and dep != t.id:
                indegree[t.id] += 1
                dependents[dep].append(t.id)

    # Preserve original order among ready tasks for determinism.
    ready = [t.id for t in tasks if indegree[t.id] == 0]
    ordered: list[str] = []
    while ready:
        tid = ready.pop(0)
        ordered.append(tid)
        for child in dependents[tid]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)

    if len(ordered) < len(tasks):
        # Cycle (or unresolved): append the rest in original order.
        seen = set(ordered)
        ordered.extend(t.id for t in tasks if t.id not in seen)

    return [by_id[tid] for tid in ordered]
