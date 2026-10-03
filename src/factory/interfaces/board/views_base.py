"""What every board view provides: show the model, and say which keys work on it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Vertical

if TYPE_CHECKING:
    from factory.runs.board import Board


class BoardView(Vertical):
    """A tab's content. `show` is called on every refresh; it must keep the operator's
    selection and focus (focus never moves on its own)."""

    model: "Board | None" = None

    def show(self, model: "Board") -> None:
        self.model = model

    def keys(self) -> list[tuple[str, str]]:
        return []

    def back(self) -> bool:
        """Esc inside the view (close a detail, a text box); False: the board handles it."""
        return False
