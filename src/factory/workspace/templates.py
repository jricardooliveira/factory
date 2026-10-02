"""Project specification templates for common stacks."""

from __future__ import annotations

import json
from pathlib import Path

from factory.domain.project_spec import ProjectSpec
from factory.workspace.projects import normalize_slug


SUPPORTED_STACKS = {"fastapi"}


def _default_name(value: str) -> str:
    return normalize_slug(value).replace("-", " ").title()


def get_stack_template(
    stack: str,
    *,
    name: str,
    description: str | None = None,
) -> ProjectSpec:
    """Return a project spec template for a supported stack."""

    normalized_stack = stack.lower()
    if normalized_stack != "fastapi":
        supported = ", ".join(sorted(SUPPORTED_STACKS))
        raise ValueError(f"Unsupported stack: {stack}. Supported stacks: {supported}")

    return ProjectSpec(
        name=name,
        description=description or f"{name} application",
        language="Python 3.12",
        framework="FastAPI",
        database="SQLite for local development",
        orm="SQLAlchemy 2.x",
        additional_tech=[],
        architecture_style="layered (routes -> services -> repositories -> models)",
        api_style="REST with OpenAPI",
        conventions=[
            "All endpoints return JSON with consistent error format: {detail, code}",
            "Use dependency injection for database sessions",
            "Business logic lives in services, not routes",
            "All list endpoints should be paginated when returning collections",
            "Tests use FastAPI TestClient and isolated temporary databases",
        ],
        deployment="local development first",
        repo_structure="single application repository",
        infra_constraints=[
            "No external services required for the initial app",
            "Prefer standard library and declared project dependencies",
        ],
        existing_modules=[],
        existing_apis=[],
        existing_data_models=[],
        auth_model="none for initial scaffold unless explicitly requested",
        multi_tenancy="none",
        nfrs=[
            "Expose GET /health for smoke testing",
            "Keep generated app runnable from a clean checkout",
        ],
        forbidden=[
            "No GraphQL",
            "No WebSockets unless explicitly requested later",
            "No managed cloud services in the initial scaffold",
        ],
    )


def create_project_spec(
    stack: str,
    *,
    name: str | None = None,
    slug: str | None = None,
    description: str | None = None,
) -> ProjectSpec:
    """Create a project spec from a stack and display metadata."""

    project_name = name or _default_name(slug or stack)
    return get_stack_template(stack, name=project_name, description=description)


def write_project_spec(path: Path, spec: ProjectSpec) -> Path:
    """Write a project spec JSON file and return its path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(spec.model_dump(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
