"""The board's views by name: one module per tab, plus the batch view and All runs."""

from __future__ import annotations

from factory.interfaces.board.activity import ActivityView, AllRunsView
from factory.interfaces.board.batch import BatchView
from factory.interfaces.board.needs import NeedsView
from factory.interfaces.board.overview import OverviewView
from factory.interfaces.board.stories import StoriesView
from factory.interfaces.board.views_base import BoardView


def build() -> dict[str, BoardView]:
    return {"overview": OverviewView(), "needs": NeedsView(), "stories": StoriesView(),
            "activity": ActivityView(), "batch": BatchView(), "allruns": AllRunsView()}
