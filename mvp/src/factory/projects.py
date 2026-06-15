"""Factory-level project registry and scaffolding."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from factory.state.db import get_db, init_db


PROJECT_DIRS = (
    "state",
    "docs/work/tasks",
    "docs/pipeline",
    "docs/context",
    "docs/architecture/adr",
    "docs/releases",
    "repo",
)


def normalize_slug(value: str) -> str:
    """Return a filesystem and CLI friendly project slug."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise ValueError("Project slug must contain at least one letter or number")
    return slug


def _next_project_id(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT id FROM projects WHERE id LIKE 'PROJ-%'").fetchall()
    max_seen = 0
    for row in rows:
        try:
            max_seen = max(max_seen, int(row["id"].split("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    return f"PROJ-{max_seen + 1:03d}"


def _row_to_project(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _link_opencode_agents(repo_path: Path) -> None:
    agents_dir = _package_root() / ".opencode"
    target = repo_path / ".opencode"
    if target.exists() or not agents_dir.exists():
        return
    try:
        target.symlink_to(agents_dir, target_is_directory=True)
    except OSError:
        # The project can still be registered; users may copy/link agents manually.
        return


def _write_project_rules(project_dir: Path, project_id: str, slug: str, name: str) -> None:
    rules_path = project_dir / "PROJECT_RULES.md"
    if rules_path.exists():
        return
    rules_path.write_text(
        "\n".join(
            [
                f"# {name} Project Rules",
                "",
                f"- Project ID: {project_id}",
                f"- Slug: {slug}",
                "- Source root: repo/",
                "- Pipeline artifacts: docs/pipeline/",
                "- Project tasks: docs/work/tasks/",
                "",
            ]
        )
    )


def create_project(
    db_path: Path,
    *,
    factory_root: Path,
    slug: str,
    name: str | None = None,
    spec_path: Path | None = None,
    stack: str | None = None,
) -> dict[str, Any]:
    """Create a project directory and persist it in the factory registry."""

    init_db(db_path)
    normalized_slug = normalize_slug(slug)
    project_name = name or normalized_slug.replace("-", " ").title()

    with get_db(db_path) as conn:
        project_id = _next_project_id(conn)
        project_dir = factory_root / "projects" / f"{project_id}-{normalized_slug}"
        repo_path = project_dir / "repo"

        for dirname in PROJECT_DIRS:
            (project_dir / dirname).mkdir(parents=True, exist_ok=True)
        _write_project_rules(project_dir, project_id, normalized_slug, project_name)
        _link_opencode_agents(repo_path)

        # Git baseline so materialization diffs are measurable from the repo itself.
        from factory.verify import git_init

        git_init(repo_path)

        resolved_spec_path = spec_path
        if stack and not resolved_spec_path:
            from factory.spec_templates import create_project_spec, write_project_spec

            resolved_spec_path = write_project_spec(
                project_dir / "project-spec.json",
                create_project_spec(stack, name=project_name, slug=normalized_slug),
            )

        now = conn.execute("SELECT datetime('now') AS now").fetchone()["now"]
        try:
            conn.execute(
                """
                INSERT INTO projects
                    (id, slug, name, repo_path, spec_path, status, created_at, updated_at)
                VALUES
                    (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    project_id,
                    normalized_slug,
                    project_name,
                    str(repo_path),
                    str(resolved_spec_path) if resolved_spec_path else None,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Project slug already exists: {normalized_slug}") from exc

        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        project = _row_to_project(row)
        assert project is not None
        return project


def get_project(db_path: Path, ref: str) -> dict[str, Any]:
    """Resolve a project by id or slug."""

    init_db(db_path)
    normalized_ref = ref if ref.startswith("PROJ-") else normalize_slug(ref)
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ? OR slug = ?",
            (normalized_ref, normalized_ref),
        ).fetchone()
    project = _row_to_project(row)
    if not project:
        raise ValueError(f"Unknown project: {ref}")
    return project


def list_projects(db_path: Path) -> list[dict[str, Any]]:
    """Return all registered projects ordered by id."""

    init_db(db_path)
    with get_db(db_path) as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY id").fetchall()
    return [dict(row) for row in rows]
