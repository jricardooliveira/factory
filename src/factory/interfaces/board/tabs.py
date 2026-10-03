"""The board's views by name (one module per tab as they are built)."""

from __future__ import annotations

from factory.interfaces.board.views_base import BoardView


class _Simple(BoardView):
    def __init__(self, keys: list[tuple[str, str]]) -> None:
        super().__init__()
        self._keys = keys

    def keys(self) -> list[tuple[str, str]]:
        return self._keys


def build() -> dict[str, BoardView]:
    return {
        "overview": _Simple([("↑↓", "move"), ("enter", "open"), ("n", "needs you"), ("b", "batch"),
                             ("P", "pause"), ("esc", "all runs")]),
        "needs": _Simple([("↑↓", "move"), ("enter", "open"), ("o", "overview"), ("esc", "back")]),
        "stories": _Simple([("↑↓", "move"), ("enter", "open"), ("v", "board view"), ("b", "batch"),
                            ("esc", "back")]),
        "activity": _Simple([("↑↓", "scroll"), ("o", "overview"), ("esc", "back")]),
        "allruns": _Simple([("↑↓", "scroll"), ("o", "overview"), ("esc", "back")]),
    }
