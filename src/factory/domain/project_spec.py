"""Project specification — the ground truth the architect must work within."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectSpec(BaseModel):
    """Defines the technical boundaries for a project.

    This is what a real architect gets handed before making any decisions.
    The architect-agent MUST stay within these constraints.
    """

    # ── Identity ──────────────────────────────────────────────────
    name: str = Field(description="Project name")
    description: str = Field(description="What this project does, in one paragraph")

    # ── Tech Stack (what we use, period) ──────────────────────────
    language: str = Field(description="Primary language, e.g. 'Python 3.12'")
    framework: str = Field(description="Web/app framework, e.g. 'FastAPI'")
    database: str = Field(description="Primary database, e.g. 'PostgreSQL 16'")
    orm: str = Field(default="", description="ORM/query layer, e.g. 'SQLAlchemy 2.x'")
    additional_tech: list[str] = Field(
        default_factory=list,
        description="Other approved tech: cache, queue, etc. e.g. ['Redis', 'Celery']",
    )

    # ── Patterns & Conventions ────────────────────────────────────
    architecture_style: str = Field(
        default="layered",
        description="e.g. 'layered', 'hexagonal', 'microservices', 'monolith'",
    )
    api_style: str = Field(default="REST", description="e.g. 'REST', 'GraphQL', 'gRPC'")
    conventions: list[str] = Field(
        default_factory=list,
        description="Coding/design conventions the team follows",
    )

    # ── Infrastructure Constraints ────────────────────────────────
    deployment: str = Field(default="", description="e.g. 'Docker + Kubernetes', 'serverless', 'bare VM'")
    repo_structure: str = Field(default="monorepo", description="'monorepo' or 'polyrepo'")
    infra_constraints: list[str] = Field(
        default_factory=list,
        description="Hard limits: 'no new databases without approval', 'single region', etc.",
    )

    # ── Existing System Context ───────────────────────────────────
    existing_modules: list[str] = Field(
        default_factory=list,
        description="Modules/services that already exist and the architect must integrate with",
    )
    existing_apis: list[str] = Field(
        default_factory=list,
        description="Existing API endpoints/contracts the architect must respect",
    )
    existing_data_models: list[str] = Field(
        default_factory=list,
        description="Tables/schemas that already exist",
    )

    # ── Non-Functional Requirements ───────────────────────────────
    auth_model: str = Field(default="", description="e.g. 'JWT + RBAC', 'session-based', 'OAuth2'")
    multi_tenancy: str = Field(default="none", description="'none', 'row-level', 'schema-per-tenant', 'db-per-tenant'")
    nfrs: list[str] = Field(
        default_factory=list,
        description="Performance, security, availability requirements",
    )

    # ── Forbidden ─────────────────────────────────────────────────
    forbidden: list[str] = Field(
        default_factory=list,
        description="Things explicitly NOT allowed: 'no WebSockets', 'no new ORMs', etc.",
    )

    def to_architect_context(self) -> str:
        """Render as a context block the architect-agent can consume."""
        lines = [
            "## Project Specification (MUST follow — these are not suggestions)",
            "",
            f"**Project:** {self.name} — {self.description}",
            "",
            "### Tech Stack (use ONLY these)",
            f"- Language: {self.language}",
            f"- Framework: {self.framework}",
            f"- Database: {self.database}",
        ]
        if self.orm:
            lines.append(f"- ORM: {self.orm}")
        if self.additional_tech:
            lines.append(f"- Additional: {', '.join(self.additional_tech)}")

        lines += [
            "",
            "### Architecture & Patterns",
            f"- Style: {self.architecture_style}",
            f"- API: {self.api_style}",
        ]
        if self.conventions:
            lines.append("- Conventions:")
            for c in self.conventions:
                lines.append(f"  - {c}")

        if self.deployment or self.infra_constraints:
            lines += ["", "### Infrastructure"]
            if self.deployment:
                lines.append(f"- Deployment: {self.deployment}")
            lines.append(f"- Repo: {self.repo_structure}")
            if self.infra_constraints:
                lines.append("- Constraints:")
                for c in self.infra_constraints:
                    lines.append(f"  - {c}")

        if self.existing_modules or self.existing_apis or self.existing_data_models:
            lines += ["", "### Existing System (integrate, do NOT replace)"]
            if self.existing_modules:
                lines.append(f"- Modules: {', '.join(self.existing_modules)}")
            if self.existing_apis:
                lines.append(f"- APIs: {', '.join(self.existing_apis)}")
            if self.existing_data_models:
                lines.append(f"- Data models: {', '.join(self.existing_data_models)}")

        if self.auth_model or self.multi_tenancy != "none" or self.nfrs:
            lines += ["", "### Non-Functional Requirements"]
            if self.auth_model:
                lines.append(f"- Auth: {self.auth_model}")
            if self.multi_tenancy != "none":
                lines.append(f"- Multi-tenancy: {self.multi_tenancy}")
            if self.nfrs:
                for nfr in self.nfrs:
                    lines.append(f"- {nfr}")

        if self.forbidden:
            lines += ["", "### Forbidden (NEVER do these)"]
            for f in self.forbidden:
                lines.append(f"- ❌ {f}")

        return "\n".join(lines)
