"""`factory project ...` output."""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table

from factory.interfaces.render import output


def print_project_created(project: dict) -> None:
    output.console.print(Panel(
        f"[bold green]{project['id']}[/bold green] {project['name']}\n"
        f"Slug: {project['slug']}\n"
        f"Repo: {project['repo_path']}",
        title="Project Created",
        border_style="green",
    ))


def print_projects(projects: list[dict]) -> None:
    if not projects:
        output.console.print("[dim]No projects found. Use factory project create <slug> first.[/dim]")
        return

    table = Table(title="Projects", border_style="cyan")
    table.add_column("ID", style="cyan")
    table.add_column("Slug")
    table.add_column("Name")
    table.add_column("Status", justify="center")
    table.add_column("Repo", style="dim")
    for project in projects:
        table.add_row(
            project["id"],
            project["slug"],
            project["name"],
            project["status"],
            project["repo_path"],
        )
    output.console.print(table)


def print_project(project: dict) -> None:
    table = Table(title=f"Project {project['id']}", show_header=False, border_style="cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for key in ("id", "slug", "name", "status", "repo_path", "spec_path"):
        table.add_row(key, project.get(key) or "")
    output.console.print(table)
