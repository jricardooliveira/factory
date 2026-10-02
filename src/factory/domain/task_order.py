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


def dependency_problems(tasks: list[TaskDef]) -> list[str]:
    """Why this task graph cannot be executed in order ([] when it can).

    `order_tasks` stays forgiving so a run never crashes on bad data; this is the
    strict reading gate-1 applies, because forgiveness meant a task could be
    implemented before the task it depends on with nothing recording it.
    """
    problems: list[str] = []
    ids = [t.id for t in tasks]
    duplicates = sorted({tid for tid in ids if ids.count(tid) > 1})
    if duplicates:
        problems.append(f"duplicate task id(s): {', '.join(duplicates)}")
    known = set(ids)
    for t in tasks:
        if t.id in t.depends_on:
            problems.append(f"{t.id} depends on itself")
        unknown = [d for d in t.depends_on if d not in known]
        if unknown:
            problems.append(f"{t.id} depends on unknown task(s): {', '.join(unknown)}")

    # Whatever Kahn's sort cannot place is in (or behind) a cycle. Self-loops are
    # already reported above, so leave them out of the cycle message.
    placed: set[str] = set()
    pending = [t for t in tasks if t.id not in t.depends_on]
    progressed = True
    while progressed:
        progressed = False
        for t in list(pending):
            if all(d in placed or d not in known for d in t.depends_on):
                placed.add(t.id)
                pending.remove(t)
                progressed = True
    if pending:
        problems.append(
            "dependency cycle among: " + ", ".join(t.id for t in pending)
        )
    return problems
