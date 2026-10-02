#!/usr/bin/env python3
"""Run multiple test scenarios through the factory pipeline.

Some requests are well-defined, some are deliberately vague or ambiguous
to test gate behavior.
"""

from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console
from rich.rule import Rule

from factory.interfaces.cli.common import DB_PATH
from factory.interfaces.cli.run import RunPrinter
from factory.runs import run_pipeline

console = Console()

# ── Test scenarios ────────────────────────────────────────────────

SCENARIOS = [
    {
        "name": "✅ Clear & specific",
        "request": "Build a Python function called celsius_to_fahrenheit that takes a float and returns the converted temperature. Include input validation for values below absolute zero (-273.15°C).",
    },
    {
        "name": "⚠️ Vague request",
        "request": "Make it faster",
    },
    {
        "name": "✅ Bug report with detail",
        "request": "Bug: The parse_date function in utils.py crashes with a ValueError when the input string is '2024-13-01' (invalid month 13). It should return None for invalid dates instead of raising an exception.",
    },
    {
        "name": "⚠️ Ambiguous scope",
        "request": "Add authentication",
    },
    {
        "name": "✅ Small & well-bounded",
        "request": "Create a Python dataclass called Config with fields: host (str, default 'localhost'), port (int, default 8080), debug (bool, default False). Add a from_env() classmethod that reads CONFIG_HOST, CONFIG_PORT, CONFIG_DEBUG from environment variables with fallback to defaults.",
    },
]


def main() -> None:
    # Clean DB for fresh run
    if DB_PATH.exists():
        DB_PATH.unlink()

    repo_root = str(Path(__file__).resolve().parents[3])

    for i, scenario in enumerate(SCENARIOS, 1):
        console.print()
        console.print(Rule(f"[bold magenta]Scenario {i}/{len(SCENARIOS)}: {scenario['name']}[/bold magenta]", style="magenta"))
        console.print()

        try:
            run_pipeline(scenario["request"], opencode_cwd=repo_root, db_path=DB_PATH,
                         on_event=RunPrinter())
        except Exception as e:
            console.print(f"  [bold red]CRASH:[/bold red] {e}")

        console.print()
        time.sleep(2)  # brief pause between runs

    console.print(Rule("[bold green]All scenarios complete[/bold green]", style="green"))


if __name__ == "__main__":
    main()
