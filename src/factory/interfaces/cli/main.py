"""`factory` entry point: argv dispatch + usage.

The console script is `factory.interfaces.cli.main:main`. Every verb maps to a
`<verb>_command(args)` in its command-group module, where `args` is argv after
the verb; anything that is not a verb is a free-form request — unless it is a
single word or a short, verb-shaped phrase (`request_refusal`), which is refused
before it can spend a token.
"""

from __future__ import annotations

import difflib
import sys
from collections.abc import Callable
from typing import NoReturn

from rich.markup import escape

from factory.interfaces import render
from factory.interfaces.cli import (
    backlog,
    board,
    interview,
    project,
    review,
    run,
    selftest,
    workspace,
)
from factory.interfaces.cli.common import fail

COMMANDS: dict[str, Callable[[list[str]], None]] = {
    "project": project.project_command,
    "status": project.status_command,
    "spec": project.spec_command,
    "interview": interview.interview_command,
    "backlog": backlog.backlog_command,
    "next": backlog.next_command,
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


# (syntax after `factory`, description). `factory <verb> --help` prints the rows
# whose syntax starts with that verb, so every verb in COMMANDS needs at least one
# (tests/interfaces/cli/test_verb_help.py enforces it).
USAGE: tuple[tuple[str, str], ...] = (
    ('"Your request here"', "Run pipeline"),
    ('run --project <id> [--no-interview] "..."', "Run pipeline for project"),
    ("status [project]", "Where each project stands, and the next command to type"),
    ("interview <project>", "Define the product with the operator: writes the approved brief"),
    ('interview <project> --amend "..."', "Reopen the approved brief for one change"),
    ("interview <project> --import <file>", "Record answers from the /factory-intake skill"),
    ("backlog <project>", "Propose the story list from the brief; approve or give feedback"),
    ("next <project> [--no-interview]", "Run the next approved story in the backlog"),
    ("spec init <slug> --stack fastapi", "Create project spec"),
    ("project create <slug>", "Create/register project"),
    ("project list", "List projects"),
    ("project show <id>", "Show project"),
    ("list", "List all runs"),
    ("queue", "Show runs awaiting review / needing attention"),
    ("board [--once | --plain] [--interval S]", "Interactive board: approve/reject in place"),
    ("review <run_id>", "Review a run"),
    ("review <run_id> --raw", "Review with raw output"),
    ("replay <run_id>", "Re-run orchestration on frozen outputs (no LLM)"),
    ("visualize [--output path]", "Generate HTML flow report"),
    ("dismiss <run_id>", "Archive a run off the board"),
    ("reconcile [--older-than S]", "Fail runs stuck 'running' (dead process)"),
    ("simulate [--report path]", "Offline scenario matrix (no tokens)"),
    ("evals [--report path]", "Regression-test the agent configuration (no tokens)"),
    ("evals capture <run_id> <name>", "Freeze a real run as a permanent eval case"),
    ("metrics [--report path]", "SDLC indicators over the factory's own history"),
    ("approve <run_id>", "Approve paused run"),
    ("reject <run_id> [reason]", "Reject paused run"),
    ("retry <run_id>", "Re-drive a run that died after you answered"),
    ("tiers", "Show per-agent model tiers (leverage allocation)"),
    ("workspace", "Show $FACTORY_HOME (default ~/.factory): DB + projects"),
    ("workspace import-legacy <dir> [--dry-run]", "Move an old factory.db + projects/ into it"),
    ("doctor [--offline]", "Preflight: opencode, tier models reachable, toolchains"),
)

_HELP_FLAGS = ("-h", "--help")


def print_usage(exit_code: int = 1, verb: str | None = None) -> NoReturn:
    """Print usage — every row, or only `verb`'s rows — then exit."""
    rows = [r for r in USAGE if verb is None or r[0] == verb or r[0].startswith(f"{verb} ")]
    width = max(len(syntax) for syntax, _ in rows)
    render.console.print("[bold]Usage:[/bold]")
    for syntax, description in rows:
        padding = " " * (width - len(syntax))
        render.console.print(
            f"  factory [bold cyan]{escape(syntax)}[/bold cyan]{padding}  {description}"
        )
    sys.exit(exit_code)


# A free-form request this short whose first word is a verb is a mistyped command
# (`factory 'project list'`), not a story. Longer ones ("review the login flow
# for lockouts") are genuine requests that happen to start with a verb.
_VERB_SHAPED_MAX_WORDS = 3


def request_refusal(argv: list[str]) -> str | None:
    """Why `argv` (not a known verb) must not start a run, or None if it may.

    Anything that is not a verb is a free-form request, and a request spends
    tokens. A typo (`factory lsit`) or a quoted verb (`factory 'project list'`)
    used to start a live run; refuse them, with a suggestion, before any call.
    """
    words = " ".join(argv).split()
    if not words:
        return None
    first = words[0]
    if len(words) == 1:
        close = difflib.get_close_matches(first, COMMANDS, n=1)
        hint = f" Did you mean `factory {close[0]}`?" if close else ""
        return (
            f"'{first}' is not a factory command, and one word is not a request.{hint}"
            f' To run the pipeline, describe the change: factory "<your request>"'
        )
    if first in COMMANDS and len(words) <= _VERB_SHAPED_MAX_WORDS:
        return (
            f"'{' '.join(words)}' looks like a command passed as one quoted argument. "
            f"Did you mean `factory {' '.join(words)}` (unquoted)?"
        )
    return None


def _request_words(argv: list[str]) -> list[str]:
    """argv minus the `--spec <file>` option `request_command` also strips."""
    words, i = [], 0
    while i < len(argv):
        if argv[i] == "--spec" and i + 1 < len(argv):
            i += 2
            continue
        words.append(argv[i])
        i += 1
    return words


def main() -> None:
    if len(sys.argv) < 2:
        print_usage(exit_code=1)

    cmd = sys.argv[1]
    if cmd in ("-h", "--help", "help"):
        print_usage(exit_code=0)

    command = COMMANDS.get(cmd)
    if command is not None:
        args = sys.argv[2:]
        # No verb parses --help itself; passed through, it was EXECUTED instead
        # (`factory reject 20 --help` rejected run 20 with the reason "--help").
        if any(a in _HELP_FLAGS for a in args):
            print_usage(exit_code=0, verb=cmd)
        command(args)
        return
    refusal = request_refusal(_request_words(sys.argv[1:]))
    if refusal:
        fail(refusal)
    run.request_command(sys.argv[1:])


if __name__ == "__main__":
    main()
