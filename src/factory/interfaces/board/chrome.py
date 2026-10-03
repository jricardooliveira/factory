"""The board's fixed frame: header, lifecycle, tabs, feedback line and key bar.

Six rows of chrome, as the design handoff specifies; the views fill the rest.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from textual.content import Content
from textual.markup import escape
from textual.widget import Widget
from textual.widgets import Static


def _bar(left: Content, right: Content, width: int) -> Content:
    gap = max(1, width - left.cell_length - right.cell_length)
    return Content.assemble(left, " " * gap, right)


class HeaderBar(Widget):
    """`factory › <project> ▾` left; worker state and the local time right."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.project, self.worker, self.queued = "", "idle", 0

    def show(self, project: str, worker: str, queued: int) -> None:
        self.project, self.worker, self.queued = project, worker, queued
        self.refresh()

    def render(self) -> Content:
        left = Content.from_markup(f"[b $bright]factory[/]  [$dim]›[/]  [$accent]{escape(self.project)} ▾[/]")
        worker = {
            "running": "[$success]●[/] worker running",
            "paused": "[$warning]⏸[/] new starts paused",
            "stuck": f"[$error]■ worker stopped · {self.queued} waiting[/]   [b $accent]W[/] restart",
        }.get(self.worker, "[$dim]○ worker idle[/]")
        right = Content.from_markup(f"{worker}   [$dim]{datetime.now():%H:%M}[/]")
        return _bar(left, right, self.size.width)


class LifecycleBar(Widget):
    """`Define ✓ ─ Plan ✓ ─ Build ● ─ Release` left; `p project` right."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.phases: list[tuple[str, str]] = []

    def show(self, phases: list[tuple[str, str]]) -> None:
        self.phases = phases
        self.refresh()

    def render(self) -> Content:
        parts = [f"[$success]{n} ✓[/]" if s == "done" else f"[$accent]{n} ●[/]" if s == "now"
                 else f"[$dim]{n}[/]" for n, s in self.phases]
        left = Content.from_markup(" [$dim]─[/] ".join(parts))
        return _bar(left, Content.from_markup("[b $accent]p[/] project"), self.size.width)


class TabBar(Widget):
    """The tabs as chips with count badges, and the rule with `━` under the active one."""

    TABS = (("overview", "Overview"), ("needs", "Needs you"), ("stories", "Stories"),
            ("activity", "Activity"))

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.active, self.badges = "overview", {}

    def show(self, active: str, badges: dict[str, int]) -> None:
        self.active, self.badges = active, badges
        self.refresh()

    def render(self) -> Content:
        chips, rule, used = [], [], 0
        for key, title in self.TABS:
            badge = self.badges.get(key) or 0
            chip = (f"[b $ink on $accent] {title} [/]" if key == self.active
                    else f"[on $chip-bg] {title} [/]")
            if badge:
                chip += f"[b $ink on $warning] {badge} [/]"
            width = len(title) + 2 + (len(str(badge)) + 2 if badge else 0)
            rule.append(("━" if key == self.active else "─") * width)
            chips.append(chip)
            used += width + 2
        line1 = Content.from_markup("  ".join(chips))
        segments = []
        for (key, _title), seg in zip(self.TABS, rule):
            segments.append(f"[$accent]{seg}[/]" if key == self.active else f"[$dim]{seg}[/]")
        line2 = Content.from_markup("[$dim]──[/]".join(segments) + "[$dim]" + "─" * max(
            0, self.size.width - used) + "[/]")
        return Content("\n").join([line1, line2])


_MARKS = {"ok": "[$success]✓[/]", "info": "[$accent]●[/]", "warn": "[$warning]![/]",
          "err": "[$error]✗[/]"}
CLEAR_AFTER = timedelta(seconds=4.5)


class FeedbackLine(Static):
    """✓ done · ● started · ! refused clear after 4.5 s or the next message;
    ✗ failed stays and names its recovery key."""

    def __init__(self, *, now: Callable[[], datetime] = datetime.now, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._now = now
        self.kind, self.text, self.at = "", "", now()

    @property
    def plain(self) -> str:
        return f"{'✓●!✗'['ok info warn err'.split().index(self.kind)]} {self.text}" if self.text else ""

    def say(self, text: str, kind: str = "ok") -> None:
        self.kind, self.text, self.at = kind, text, self._now()
        self.update(Content.from_markup(f"{_MARKS[kind]} {escape(text)}"))

    def clear(self) -> None:
        self.kind = self.text = ""
        self.update("")

    def tick(self) -> None:
        if self.text and self.kind != "err" and self._now() - self.at > CLEAR_AFTER:
            self.clear()


class KeyBar(Widget):
    """The keys that work on the current screen, dropped from the right when too wide."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.keys: list[tuple[str, str]] = []

    def show(self, keys: list[tuple[str, str]]) -> None:
        self.keys = keys
        self.refresh()

    def render(self) -> Content:
        def chip(key: str, label: str) -> str:
            return f"[b $accent on $chip-off-bg] {escape(key)} [/] {escape(label)}"
        right = Content.from_markup(chip("?", "help"))
        keys = list(self.keys)
        while keys:
            left = Content.from_markup("  ".join(chip(k, v) for k, v in keys))
            if left.cell_length + right.cell_length + 2 <= self.size.width or not self.size.width:
                break
            keys.pop()
        left = Content.from_markup("  ".join(chip(k, v) for k, v in keys))
        return _bar(left, right, self.size.width)
