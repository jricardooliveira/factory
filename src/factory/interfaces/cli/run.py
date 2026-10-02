"""Commands that drive a run: a free-form request, run, approve, reject, retry, replay.

Each one calls `factory.runs` and renders its events live through `RunPrinter`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import ValidationError

from factory import runs
from factory.domain.project_spec import ProjectSpec
from factory.interfaces import render
from factory.interfaces.cli.common import db_path, fail, run_id_arg
from factory.runs import (
    NodeCompleted,
    ResumeEntered,
    RetryStarted,
    RunError,
    RunEvent,
    RunFinished,
    RunStarted,
)
from factory.runs import queries


class RunPrinter:
    """Renders run-service events as they arrive.

    A resumed run renders its nodes more tersely than a fresh one (see
    `render.print_resume_node`), so the printer is told which it is watching.
    """

    def __init__(self, *, resumed: bool = False) -> None:
        self.resumed = resumed
        self.retry_started = False

    def __call__(self, event: RunEvent) -> None:
        if isinstance(event, RunStarted):
            render.print_run_started(event)
        elif isinstance(event, RetryStarted):
            self.retry_started = True
            render.print_retry_started(event)
        elif isinstance(event, ResumeEntered):
            render.print_resume_entered(event)
        elif isinstance(event, NodeCompleted):
            node_printer = render.print_resume_node if self.resumed else render.print_run_node
            node_printer(event.node, event.output)
        elif isinstance(event, RunFinished):
            outcome = event.outcome
            logs, gates = queries.run_record(outcome.run_id, db_path=outcome.db_path)
            render.print_run_finished(outcome, logs, gates)


def request_command(args: list[str]) -> None:
    """`factory "<request>" [--spec path.json]`: a one-off run outside any project."""
    spec_file = None
    filtered_args = []
    i = 0
    while i < len(args):
        if args[i] == "--spec" and i + 1 < len(args):
            spec_file = args[i + 1]
            i += 2
        else:
            filtered_args.append(args[i])
            i += 1

    request = " ".join(filtered_args).strip()
    if not request:
        fail('Empty request. Usage: factory "<your request>"')

    # Load project spec if provided
    project_spec_text = None
    if spec_file:
        spec_path = Path(spec_file)
        if not spec_path.exists():
            fail(f"Spec file not found: {spec_file}")
        try:
            spec_data = json.loads(spec_path.read_text())
            project_spec = ProjectSpec.model_validate(spec_data)
        except json.JSONDecodeError as exc:
            fail(f"Spec file is not valid JSON: {exc}")
        except ValidationError as exc:
            render.console.print(
                f"[red]Spec file does not match the project spec schema:[/red]\n{exc}"
            )
            sys.exit(1)
        project_spec_text = project_spec.to_architect_context()

    runs.run_pipeline(
        request, project_spec_text=project_spec_text, db_path=db_path(), on_event=RunPrinter()
    )


def run_command(args: list[str]) -> None:
    """`factory run --project <id> "<request>"`: run against a registered project."""
    if len(args) < 3 or args[0] != "--project":
        fail('Usage: factory run --project <project-id-or-slug> "Your request here"')

    project_ref = args[1]
    request = " ".join(args[2:]).strip()
    if not request:
        fail("Run request cannot be empty")

    try:
        runs.run_project_pipeline(project_ref, request, db_path=db_path(), on_event=RunPrinter())
    except ValueError as exc:
        fail(str(exc))


def replay_command(args: list[str]) -> None:
    run_id = run_id_arg(args, "Usage: factory replay <run_id>")
    try:
        runs.replay_run(run_id, db_path=db_path(), on_event=RunPrinter())
    except RunError as exc:
        fail(str(exc))


def approve_command(args: list[str]) -> None:
    run_id = run_id_arg(args, "Usage: factory approve <run_id> [note]")
    reason = " ".join(args[1:]) if len(args) > 1 else None
    _resume(run_id, "approve", reason)


def reject_command(args: list[str]) -> None:
    run_id = run_id_arg(args, 'Usage: factory reject <run_id> "<feedback>"')
    reason = " ".join(args[1:]).strip() if len(args) > 1 else ""
    if not reason:
        fail('Reject requires feedback: factory reject <run_id> "<feedback>"')
    _resume(run_id, "reject", reason)


def _resume(run_id: int, action: str, reason: str | None) -> None:
    # A refused resume (wrong status, no pending gate, missing logs) has always
    # been reported without a non-zero exit; keep that.
    try:
        runs.resume_run(
            run_id, action, reason=reason, db_path=db_path(), on_event=RunPrinter(resumed=True)
        )
    except RunError as exc:
        render.print_error(str(exc))


def retry_command(args: list[str]) -> None:
    run_id = run_id_arg(args, "Usage: factory retry <run_id>")
    printer = RunPrinter(resumed=True)
    try:
        runs.retry_run(run_id, db_path=db_path(), on_event=printer)
    except RunError as exc:
        if not printer.retry_started:
            fail(str(exc))  # retry itself refused: nothing was re-opened
        # The decision was re-opened and the RESUME refused (e.g. missing logs):
        # reported like any refused resume, without a non-zero exit.
        render.print_error(str(exc))
