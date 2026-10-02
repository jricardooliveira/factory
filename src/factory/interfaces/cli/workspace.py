"""`factory workspace`: where the factory's state lives, and the one-off legacy import."""

from __future__ import annotations

from pathlib import Path

from factory.interfaces import render
from factory.interfaces.cli.common import db_path, fail
from factory.workspace import home, projects_dir
from factory.workspace.layout import FACTORY_HOME_ENV
from factory.workspace.legacy import LegacyImportError, import_legacy
from factory.workspace.projects import list_projects

USAGE = "Usage: factory workspace [import-legacy <old_dir> [--dry-run]]"


def workspace_command(args: list[str]) -> None:
    """`factory workspace` prints the resolved home; `import-legacy` moves old state in."""
    if not args:
        show_workspace()
    elif args[0] == "import-legacy":
        import_legacy_command(args[1:])
    else:
        fail(f"Unknown workspace command: {args[0]}. {USAGE}")


def show_workspace() -> None:
    """Home, DB and projects — read-only: a fresh home is reported, not created."""
    db = db_path()
    projects = list_projects(db) if db.is_file() else []
    rows = [
        {**p, "present": Path(p["repo_path"]).is_dir() if p.get("repo_path") else False}
        for p in projects
    ]
    render.print_workspace(
        home=home(),
        env_var=FACTORY_HOME_ENV,
        db_path=db,
        db_exists=db.is_file(),
        projects_dir=projects_dir(),
        projects=rows,
    )


def import_legacy_command(args: list[str]) -> None:
    """`factory workspace import-legacy <old_dir> [--dry-run]`."""
    positional = [a for a in args if not a.startswith("--")]
    unknown = [a for a in args if a.startswith("--") and a != "--dry-run"]
    if unknown:
        fail(f"Unknown import-legacy option: {unknown[0]}. {USAGE}")
    if len(positional) != 1:
        fail(USAGE)
    try:
        result = import_legacy(Path(positional[0]), dry_run="--dry-run" in args)
    except LegacyImportError as exc:
        fail(f"Legacy import refused (nothing was moved): {exc}")
    render.print_legacy_import(result)
