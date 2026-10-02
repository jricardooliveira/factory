"""`factory workspace` and the legacy-import report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.panel import Panel
from rich.table import Table

from factory.interfaces.render import output


def print_workspace(
    *,
    home: Path,
    env_var: str,
    db_path: Path,
    db_exists: bool,
    projects_dir: Path,
    projects: list[dict],
) -> None:
    """Where the factory's state lives (`factory workspace`)."""
    db_note = "" if db_exists else "  [dim](not created yet)[/dim]"
    output.console.print(Panel(
        f"[bold]Home[/bold]      {home}  [dim](${env_var}, default ~/.factory)[/dim]\n"
        f"[bold]Database[/bold]  {db_path}{db_note}\n"
        f"[bold]Projects[/bold]  {projects_dir}",
        title="Factory workspace",
        border_style="cyan",
    ))
    if not projects:
        output.console.print("[dim]No projects registered. Use factory project create <slug>.[/dim]")
        return
    table = Table(title="Projects", border_style="cyan")
    table.add_column("ID", style="cyan")
    table.add_column("Slug")
    table.add_column("Status", justify="center")
    table.add_column("Repository", overflow="fold")
    for project in projects:
        where = project.get("repo_path") or ""
        if not project.get("present"):
            where = f"{where} [red](missing)[/red]"
        table.add_row(project["id"], project["slug"], project["status"], where)
    output.console.print(table)


def print_legacy_import(result: Any) -> None:
    """What `factory workspace import-legacy` moved (or, with --dry-run, would move)."""
    verb = "Would move" if result.dry_run else "Moved"
    lines = [f"{verb} {result.db_source} -> {result.db_target}"]
    for move in result.projects:
        source = move.repo_source or move.source
        lines.append(f"{verb} {move.project_id} {source} -> {move.target}")
        if move.evidence:
            lines.append(f"    evidence merged into the repo: {len(move.evidence)} file(s)")
        if move.spec_source is not None:
            lines.append(f"    spec copied in as project-spec.json from {move.spec_source}")
        if move.left_behind:
            lines.append(f"    [yellow]left in place (not evidence):[/yellow] "
                         f"{', '.join(move.left_behind)}")
        for note in move.notes:
            lines.append(f"    [yellow]note:[/yellow] {note}")
    for missing in result.missing:
        lines.append(f"[yellow]Registered but not found:[/yellow] {missing}")
    for folder in result.unregistered:
        lines.append(f"[yellow]Not registered, left in place:[/yellow] {folder}")
    title = "Legacy import (dry run — nothing moved)" if result.dry_run else "Legacy import"
    output.console.print(Panel("\n".join(lines), title=title,
                        border_style="cyan" if result.dry_run else "green"))
