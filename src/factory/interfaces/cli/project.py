"""Commands that set up work: `factory project ...` and `factory spec ...`."""

from __future__ import annotations

from pathlib import Path

from factory.interfaces import render
from factory.interfaces.cli.common import db_path, fail
from factory.state.db import init_db
from factory.workspace import home
from factory.workspace.projects import create_project, get_project
from factory.workspace.projects import list_projects as fetch_projects
from factory.workspace.templates import create_project_spec, write_project_spec


def project_command(args: list[str]) -> None:
    """Dispatch project subcommands."""
    if not args:
        fail("Usage: factory project <create|list|show> ...")

    subcommand = args[0]
    if subcommand == "create":
        create_project_command(args[1:])
    elif subcommand == "list":
        render.print_projects(fetch_projects(db_path()))
    elif subcommand == "show":
        if len(args) < 2:
            fail("Usage: factory project show <project-id-or-slug>")
        show_project_command(args[1])
    else:
        fail(f"Unknown project command: {subcommand}")


def create_project_command(args: list[str]) -> None:
    """Create and register a factory project."""
    if not args:
        fail("Usage: factory project create <slug> [--name NAME] [--spec PATH] [--stack fastapi]")

    slug = args[0]
    name = None
    spec_path = None
    stack = None
    i = 1
    while i < len(args):
        if args[i] == "--name" and i + 1 < len(args):
            name = args[i + 1]
            i += 2
        elif args[i] == "--spec" and i + 1 < len(args):
            spec_path = Path(args[i + 1]).resolve()
            i += 2
        elif args[i] == "--stack" and i + 1 < len(args):
            stack = args[i + 1]
            i += 2
        else:
            fail(f"Unknown project create option: {args[i]}")

    init_db(db_path())
    try:
        project = create_project(
            db_path(),
            home=home(),
            slug=slug,
            name=name,
            spec_path=spec_path,
            stack=stack,
        )
    except ValueError as exc:
        fail(str(exc))
    render.print_project_created(project)


def show_project_command(ref: str) -> None:
    """Print one registered project."""
    try:
        project = get_project(db_path(), ref)
    except ValueError as exc:
        fail(str(exc))
    render.print_project(project)


def spec_command(args: list[str]) -> None:
    """Dispatch spec subcommands."""
    if not args:
        fail("Usage: factory spec init <slug> --stack fastapi")

    subcommand = args[0]
    if subcommand == "init":
        spec_init_command(args[1:])
    else:
        fail(f"Unknown spec command: {subcommand}")


def spec_init_command(args: list[str]) -> None:
    """Create a project specification JSON file from a stack template."""
    if not args:
        fail("Usage: factory spec init <slug> --stack fastapi [--name NAME] [--output PATH]")

    slug = args[0]
    name = None
    output = Path("project-spec.json")
    stack = None
    i = 1
    while i < len(args):
        if args[i] == "--stack" and i + 1 < len(args):
            stack = args[i + 1]
            i += 2
        elif args[i] == "--name" and i + 1 < len(args):
            name = args[i + 1]
            i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            output = Path(args[i + 1])
            i += 2
        else:
            fail(f"Unknown spec init option: {args[i]}")

    if not stack:
        fail("Missing required option: --stack fastapi")

    try:
        path = write_project_spec(
            output,
            create_project_spec(stack, name=name, slug=slug),
        )
    except ValueError as exc:
        fail(str(exc))
    render.print_created(path, "Project Spec Created")
