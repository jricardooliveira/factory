"""`factory` entry point: argv dispatch + usage.

The console script is `factory.interfaces.cli.main:main`. Every verb maps to a
`<verb>_command(args)` in its command-group module, where `args` is argv after
the verb; anything that is not a verb is a free-form request.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import NoReturn

from factory.interfaces.cli import board, project, review, run, selftest, workspace
from factory.interfaces.render import console

COMMANDS: dict[str, Callable[[list[str]], None]] = {
    "project": project.project_command,
    "spec": project.spec_command,
    "run": run.run_command,
    "approve": run.approve_command,
    "reject": run.reject_command,
    "retry": run.retry_command,
    "replay": run.replay_command,
    "review": review.review_command,
    "list": review.list_command,
    "queue": review.queue_command,
    "dismiss": review.dismiss_command,
    "reconcile": review.reconcile_command,
    "simulate": selftest.simulate_command,
    "evals": selftest.evals_command,
    "metrics": selftest.metrics_command,
    "tiers": selftest.tiers_command,
    "doctor": selftest.doctor_command,
    "board": board.board_command,
    "visualize": board.visualize_command,
    "workspace": workspace.workspace_command,
}


def print_usage(exit_code: int = 1) -> NoReturn:
    console.print("[bold]Usage:[/bold]")
    console.print("  factory [bold cyan]\"Your request here\"[/bold cyan]          Run pipeline")
    console.print("  factory [bold cyan]run --project <id> \"...\"[/bold cyan]      Run pipeline for project")
    console.print("  factory [bold cyan]spec init <slug> --stack fastapi[/bold cyan] Create project spec")
    console.print("  factory [bold cyan]project create <slug>[/bold cyan]          Create/register project")
    console.print("  factory [bold cyan]project list[/bold cyan]                   List projects")
    console.print("  factory [bold cyan]project show <id>[/bold cyan]              Show project")
    console.print("  factory [bold cyan]list[/bold cyan]                           List all runs")
    console.print("  factory [bold cyan]queue[/bold cyan]                          Show runs awaiting review / needing attention")
    console.print("  factory [bold cyan]board[/bold cyan]                          Interactive board: approve/reject in place ([dim]--once / --plain[/dim])")
    console.print("  factory [bold cyan]review <run_id>[/bold cyan]               Review a run")
    console.print("  factory [bold cyan]review <run_id> --raw[/bold cyan]         Review with raw output")
    console.print("  factory [bold cyan]replay <run_id>[/bold cyan]               Re-run orchestration on frozen outputs (no LLM)")
    console.print("  factory [bold cyan]visualize [--output path][/bold cyan]      Generate HTML flow report")
    console.print("  factory [bold cyan]dismiss <run_id>[/bold cyan]              Archive a run off the board")
    console.print("  factory [bold cyan]reconcile [--older-than S][/bold cyan]     Fail runs stuck 'running' (dead process)")
    console.print("  factory [bold cyan]simulate [--report path][/bold cyan]        Offline scenario matrix (no tokens)")
    console.print("  factory [bold cyan]evals [--report path][/bold cyan]           Regression-test the agent configuration (no tokens)")
    console.print("  factory [bold cyan]evals capture <run_id> <name>[/bold cyan]   Freeze a real run as a permanent eval case")
    console.print("  factory [bold cyan]metrics [--report path][/bold cyan]         SDLC indicators over the factory's own history")
    console.print("  factory [bold cyan]approve <run_id>[/bold cyan]              Approve paused run")
    console.print("  factory [bold cyan]reject <run_id> [reason][/bold cyan]      Reject paused run")
    console.print("  factory [bold cyan]retry <run_id>[/bold cyan]                Re-drive a run that died after you answered")
    console.print("  factory [bold cyan]tiers[/bold cyan]                          Show per-agent model tiers (leverage allocation)")
    console.print("  factory [bold cyan]workspace[/bold cyan]                      Show $FACTORY_HOME (default ~/.factory): DB + projects")
    console.print("  factory [bold cyan]workspace import-legacy <dir>[/bold cyan]  Move an old factory.db + projects/ into it ([dim]--dry-run[/dim])")
    console.print("  factory [bold cyan]doctor [--offline][/bold cyan]             Preflight: opencode, tier models reachable, toolchains")
    sys.exit(exit_code)


def main() -> None:
    if len(sys.argv) < 2:
        print_usage(exit_code=1)

    cmd = sys.argv[1]
    if cmd in ("-h", "--help", "help"):
        print_usage(exit_code=0)

    command = COMMANDS.get(cmd)
    if command is not None:
        command(sys.argv[2:])
    else:
        run.request_command(sys.argv[1:])


if __name__ == "__main__":
    main()
