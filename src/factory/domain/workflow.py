"""Pure readiness and conservative conflict policy for independently prepared stories."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResourceClaim(BaseModel):
    """Semantic resources supplement file scopes; different files can share a contract."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["file", "component", "api", "data", "config", "decision"]
    name: str
    mode: Literal["read", "write"] = "write"

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip().replace("\\", "/")
        if not value or value.startswith("/") or ".." in PurePosixPath(value).parts:
            raise ValueError("Resource names must be nonempty, project-relative names")
        return value.rstrip("/") or "*"


class StoryPlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    project_id: str
    backlog_id: int
    revision: int = Field(ge=1)
    context_revision: str
    base_commit: str
    request: str
    resources: tuple[ResourceClaim, ...] = ()
    dependencies: tuple[int, ...] = ()
    uncertainties: tuple[str, ...] = ()
    rationale: str = ""
    spec: dict = Field(default_factory=dict)
    architecture: dict = Field(default_factory=dict)
    context: dict = Field(default_factory=dict)
    ready: bool = False
    budget_usd: float = Field(default=10.0, gt=0, allow_inf_nan=False)


@dataclass(frozen=True)
class BatchAssessment:
    selected: tuple[StoryPlan, ...]
    excluded: dict[int, tuple[str, ...]] = field(default_factory=dict)


def _overlap(left: str, right: str) -> bool:
    # Any wildcard is conservatively interpreted as its directory prefix. It is
    # preferable to serialize uncertain scopes than falsely promise independence.
    def prefix(value: str) -> str:
        indices = [value.index(c) for c in "*?[" if c in value]
        if indices:
            return value[:min(indices)].rsplit("/", 1)[0] if "/" in value[:min(indices)] else ""
        return value.rstrip("/")
    a, b = prefix(left), prefix(right)
    return not a or not b or a == b or a.startswith(b + "/") or b.startswith(a + "/")


def conflicts(left: StoryPlan, right: StoryPlan) -> tuple[str, ...]:
    reasons = []
    for a in left.resources:
        for b in right.resources:
            if a.kind == b.kind and (a.mode == "write" or b.mode == "write"):
                if _overlap(a.name, b.name):
                    reasons.append(f"Shared {a.kind}: {a.name} / {b.name}")
    return tuple(dict.fromkeys(reasons))


def assess_batch(
    plans: list[StoryPlan], *, context_revision: str, base_commit: str,
    completed: frozenset[int] = frozenset(), active: tuple[StoryPlan, ...] = (),
    limit: int = 2,
) -> BatchAssessment:
    """Greedily select compatible ready plans; dependencies must be integrated already."""
    if limit < 1:
        raise ValueError("Concurrency limit must be positive")
    selected: list[StoryPlan] = []
    excluded: dict[int, tuple[str, ...]] = {}
    ids = [p.backlog_id for p in plans]
    graph = {p.backlog_id: p.dependencies for p in plans}

    def cyclic(node: int, path: frozenset[int]) -> bool:
        if node in path:
            return True
        return any(cyclic(dep, path | {node}) for dep in graph.get(node, ()) if dep in graph)

    for plan in plans:
        reasons = []
        if ids.count(plan.backlog_id) != 1:
            reasons.append("Multiple revisions supplied for this story")
        if not plan.ready:
            reasons.append("Refinement is incomplete")
        if not plan.resources or not any(r.kind == "file" for r in plan.resources):
            reasons.append("File impact is unknown")
        if not any(r.kind != "file" for r in plan.resources):
            reasons.append("Component/contract impact is unknown")
        reasons.extend(plan.uncertainties)
        if not context_revision or plan.context_revision != context_revision:
            reasons.append("Project decisions changed; refine the plan again")
        if not base_commit or plan.base_commit != base_commit:
            reasons.append("Project baseline changed; reassess the plan")
        if cyclic(plan.backlog_id, frozenset()):
            reasons.append("Dependency cycle")
        missing = set(plan.dependencies) - completed
        if missing:
            reasons.append("Waiting for integrated stories: " + ", ".join(map(str, sorted(missing))))
        for other in (*active, *selected):
            if other.project_id != plan.project_id:
                continue
            if other.backlog_id == plan.backlog_id:
                reasons.append("Story already has a reserved workspace")
            reasons.extend(f"Story {other.backlog_id}: {reason}" for reason in conflicts(plan, other))
        if len(selected) + len(active) >= limit:
            reasons.append("Concurrency capacity is reserved")
        if reasons:
            excluded[plan.backlog_id] = tuple(dict.fromkeys(reasons))
        else:
            selected.append(plan)
    return BatchAssessment(tuple(selected), excluded)


def permits_file(plan: StoryPlan, path: str) -> bool:
    """Batch claims include tests and manifests: no implicit shared-file exemptions."""
    import fnmatch
    normalized = path.removeprefix('./').removeprefix('repo/')
    if normalized.startswith('/') or '..' in PurePosixPath(normalized).parts:
        return False
    return any(r.kind == 'file' and r.mode == 'write' and (
        normalized == r.name or normalized.startswith(r.name.rstrip('/') + '/')
        or fnmatch.fnmatchcase(normalized, r.name)) for r in plan.resources)


def design_conflicts(plan: StoryPlan, modules: list[str], *, api: bool, data: bool,
                     dependencies: bool) -> list[str]:
    reasons = [f'Design file outside the approved batch: {p}' for p in modules
               if not permits_file(plan, p)]
    kinds = {r.kind for r in plan.resources if r.mode == 'write'}
    for required, impacted in (('api', api), ('data', data), ('config', dependencies)):
        if impacted and required not in kinds:
            reasons.append(f'Design adds {required} impact absent from the batch assessment')
    return reasons
