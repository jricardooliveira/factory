"""Factory-level project registry and scaffolding.

A project lives at ``$FACTORY_HOME/projects/<slug>/`` and that directory IS its
git repository: code at the root, the factory's evidence under ``docs/``,
``PROJECT_RULES.md`` and ``project-spec.json`` beside the code. It used to be
split in two — code in ``projects/<P>/repo/`` and its audit trail one level up,
outside that repository — so a product's history lived partly in the factory.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

from factory.agent_config.location import checkout_root
from factory.state.db import get_db, init_db
from factory.workspace import layout
from factory.workspace.git import git_commit_paths, git_init
from factory.workspace.layout import normalize_slug
from factory.workspace.templates import create_project_spec, write_project_spec

RULES_FILENAME = "PROJECT_RULES.md"
SPEC_FILENAME = "project-spec.json"


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


def factory_opencode_dir() -> Path:
    """This checkout's ``.opencode/`` — what every product's ``.opencode`` links to."""
    return checkout_root() / ".opencode"


def link_opencode_agents(repo_path: Path) -> None:
    """Point <repo>/.opencode at the factory's agent definitions.

    An existing SYMLINK is re-pointed (a repo moved by `import-legacy` still
    links to wherever the factory used to live); a real directory is left alone.
    """
    agents_dir = factory_opencode_dir()
    target = repo_path / ".opencode"
    if not agents_dir.exists():
        return
    if target.is_symlink():
        if target.resolve() == agents_dir.resolve():
            return
        target.unlink()
    elif target.exists():
        return
    try:
        target.symlink_to(agents_dir, target_is_directory=True)
    except OSError:
        # The project can still be registered; users may copy/link agents manually.
        return


def render_project_rules(project_id: str, slug: str, name: str) -> str:
    """The scaffolded PROJECT_RULES.md.

    Agents read this file (it heads the project-memory block), so what it says
    about paths is prompt text: "Source root: repo/" is what taught them to
    prefix every path with ``repo/``. It now names the repo root, and lists the
    paths the factory owns so no agent tries to author them.
    """
    owned = ", ".join(f"`{p}`" for p in layout.EVIDENCE_PATHS)
    return "\n".join(
        [
            f"# {name} Project Rules",
            "",
            f"- Project ID: {project_id}",
            f"- Slug: {slug}",
            "- Source root: the repository root (write repo-relative paths, e.g. `app/main.py`)",
            f"- Factory-owned evidence (never write these): {owned}",
            "",
        ]
    )


def _write_project_rules(project_dir: Path, project_id: str, slug: str, name: str) -> None:
    rules_path = project_dir / RULES_FILENAME
    if rules_path.exists():
        return
    rules_path.write_text(render_project_rules(project_id, slug, name), encoding="utf-8")


def create_project(
    db_path: Path,
    *,
    home: Path | None = None,
    slug: str,
    name: str | None = None,
    spec_path: Path | None = None,
    stack: str | None = None,
) -> dict[str, Any]:
    """Create a project repository under <home>/projects/<slug>/ and register it.

    `home` defaults to $FACTORY_HOME. An explicit `spec_path` is COPIED into the
    repo as project-spec.json (so the spec the architect designed against is part
    of the product's history); otherwise `stack` writes the stack template there.
    Either way the scaffold is committed as the repo's first ``factory:`` commit.
    """

    init_db(db_path)
    normalized_slug = normalize_slug(slug)
    project_name = name or normalized_slug.replace("-", " ").title()
    root = Path(home) if home is not None else layout.home()
    project_dir = root / "projects" / normalized_slug

    with get_db(db_path) as conn:
        # Refuse BEFORE touching disk, so a refusal never leaves a half-made repo.
        taken = conn.execute(
            "SELECT 1 FROM projects WHERE slug = ?", (normalized_slug,)
        ).fetchone()
        if taken:
            raise ValueError(f"Project slug already exists: {normalized_slug}")
        if project_dir.exists() and any(project_dir.iterdir()):
            raise ValueError(f"Project directory already exists and is not empty: {project_dir}")
        if spec_path is not None and not Path(spec_path).is_file():
            raise ValueError(f"Project spec not found: {spec_path}")

        project_id = _next_project_id(conn)
        project_dir.mkdir(parents=True, exist_ok=True)

        git_init(project_dir)
        _write_project_rules(project_dir, project_id, normalized_slug, project_name)
        link_opencode_agents(project_dir)

        resolved_spec_path: Path | None = None
        if spec_path is not None:
            resolved_spec_path = project_dir / SPEC_FILENAME
            shutil.copyfile(spec_path, resolved_spec_path)
        elif stack:
            resolved_spec_path = write_project_spec(
                project_dir / SPEC_FILENAME,
                create_project_spec(stack, name=project_name, slug=normalized_slug),
            )

        # The scaffold is the repo's first factory: commit — the baseline every
        # later measurement of the factory's CODE change is taken from.
        git_commit_paths(
            project_dir,
            [RULES_FILENAME, SPEC_FILENAME],
            f"factory: scaffold {project_id} {normalized_slug}",
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
                    str(project_dir),
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
