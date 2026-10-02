"""What every command module shares: where the DB is, and argv helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NoReturn

from factory.interfaces.render import print_error
from factory.workspace import db_path

# The DB is $FACTORY_HOME/factory.db (default ~/.factory), resolved at CALL time —
# never relative to the working directory, and never frozen at import, so a test
# (or an operator) that sets FACTORY_HOME is always honoured.
__all__ = ["db_path", "fail", "report_path", "run_id_arg"]


def fail(message: str) -> NoReturn:
    """Print `message` in red and exit 1."""
    print_error(message)
    sys.exit(1)


def run_id_arg(args: list[str], usage: str) -> int:
    """Parse args[0] (the token after the verb) as a run id, or exit with a friendly error."""
    if not args:
        fail(usage)
    try:
        return int(args[0])
    except ValueError:
        fail(f"Run id must be a number, got '{args[0]}'. {usage}")


def report_path(args: list[str], default: str) -> Path | None:
    """`--report [path]` → the path to write (default name if none given), else None."""
    if "--report" not in args:
        return None
    idx = args.index("--report")
    return Path(args[idx + 1]) if idx + 1 < len(args) else Path(default)
