"""`factory board`: the board app (`board_app.BoardApp`, the design handoff's 1a) plus the
operator's other verbs on ctrl+p — doctor, evals, simulate, metrics, model tiers,
reconcile, retry / replay a run, a new project, and the direct one-story runs.

Those verbs never appear in the footer (operator decision, 2026-10-03). A command's
text opens in a read-only modal; a failure is said, never a crash. The direct story
runs ask their story questions in a blocking modal (`interview_screen.QuestionScreen`,
the same pick list as Needs you) from a worker thread.
"""

from __future__ import annotations

import queue
from pathlib import Path

from textual import work
from textual.app import SystemCommand
from textual.screen import Screen
from textual.worker import get_current_worker

from factory.domain.interview import InterviewQuestion
from factory.evidence.backlog import BACKLOG_RELPATH
from factory.evidence.brief import load_brief
from factory.interfaces.board.board_app import BoardApp, BoardScreen
from factory.interfaces.board.interview_screen import PromptScreen, QuestionScreen, ReviewScreen
from factory.interfaces.render.status import status_lines
from factory.preflight.doctor import run_doctor
from factory.runs import (
    RunError, all_project_status, has_brief, next_story, project_status, reconcile_stale,
    replay_run, retry_run, run_backlog_story, run_project_pipeline, run_story_interview,
)
from factory.workspace.projects import create_project, get_project


class FactoryBoard(BoardApp):
    def get_system_commands(self, screen: Screen):
        yield from super().get_system_commands(screen)
        commands = [
            ("Project status", "factory status: where each project stands + next command",
             lambda: self._job("Status", self._status_text)),
            ("New project", "factory project create <slug>", self._menu_new_project),
            ("Brief & backlog: show", "the approved brief and story list", self._show_brief),
            ("Story: run next from backlog", "factory next <project>: one story, straight into the pipeline",
             lambda: self._job("Next story", self._next_text, self._project())),
            ("Story: run a new request", "factory run --project <project>", self._menu_story),
            ("Run: retry", "factory retry <run>", lambda: self._menu_run("retry")),
            ("Run: replay", "factory replay <run> (zero tokens)", lambda: self._menu_run("replay")),
            ("Runs: reconcile stale", "factory reconcile", lambda: self._job("Reconcile", lambda: (
                f"Marked failed: {ids}" if (ids := reconcile_stale(db_path=self.db_path))
                else "No stale runs."))),
            ("Doctor (offline)", "factory doctor --offline",
             lambda: self._job("Doctor", self._doctor_text, True)),
            ("Doctor (probe models)", "factory doctor (one paid probe per model)",
             lambda: self._job("Doctor", self._doctor_text, False)),
            ("Evals", "factory evals (offline)", lambda: self._job("Evals", self._evals_text)),
            ("Simulate", "factory simulate (offline)", lambda: self._job("Simulate", self._simulate_text)),
            ("Metrics", "factory metrics", lambda: self._job("Metrics", self._metrics_text)),
            ("Model tiers", "factory tiers", lambda: self._job("Model tiers", self._tiers_text)),
        ]
        for title, help_text, callback in commands:
            yield SystemCommand(title, help_text, callback)

    # ── helpers ────────────────────────────────────────────────────
    def _board(self) -> BoardScreen | None:
        return next((s for s in self.screen_stack if isinstance(s, BoardScreen)), None)

    def _project(self) -> str | None:
        board = self._board()
        return board.project if board else self.project

    def _say(self, text: str, kind: str = "ok") -> None:
        board = self._board()
        if board is not None:
            board.say(text, kind)

    @work(thread=True, group="palette")
    def _job(self, title: str, fn, *args) -> None:
        """Run one command off the UI thread; show its text, or say its error in red."""
        try:
            text = fn(*args)
        except Exception as exc:  # a refusal or a crash: said, never kill the board
            self.call_from_thread(self._say, f"{title}: {exc}", "err")
            return
        if text:
            self.call_from_thread(self.push_screen, ReviewScreen(title, text, review=False))

    def _prompt(self, title: str, placeholder: str, then) -> None:
        self.push_screen(PromptScreen(title, placeholder), lambda value: value and then(value))

    def _modal(self, screen: Screen):
        """Show `screen` and block this worker thread until it is dismissed."""
        answer: queue.Queue = queue.Queue()
        self.call_from_thread(self.push_screen, screen, answer.put)
        worker = get_current_worker()
        while True:
            try:
                return answer.get(timeout=0.1)
            except queue.Empty:
                if worker.is_cancelled:  # the board is closing: unblock the thread
                    raise RunError("Interview closed; the answers so far are saved.") from None

    def _ask(self, question: InterviewQuestion, missing: list[str]) -> str | None:
        return self._modal(QuestionScreen(question, missing))

    # ── commands ───────────────────────────────────────────────────
    def _status_text(self) -> str:
        ref = self._project()
        statuses = ([project_status(ref, db_path=self.db_path)] if ref
                    else all_project_status(db_path=self.db_path))
        if not statuses:
            return "No projects yet: ctrl+p → New project."
        return "\n\n".join("\n".join(status_lines(s)) for s in statuses)

    def _menu_new_project(self) -> None:
        def create(slug: str) -> None:
            try:
                project = create_project(self.db_path, slug=slug)
            except (ValueError, OSError) as exc:
                self._say(str(exc), "err")
                return
            board = self._board()
            if board is not None:
                board.switch_project(project["slug"])
            self._say(f"{project['slug']} created. Press i to start the interview.", "ok")
        self._prompt("New project", "slug, e.g. habits", create)

    def _show_brief(self) -> None:
        ref = self._project()
        if not ref:
            self._say("Choose a project first: p.", "warn")
            return
        repo = Path(get_project(self.db_path, ref)["repo_path"])
        backlog = repo / BACKLOG_RELPATH
        body = (load_brief(repo) or "(no approved brief yet: press i to interview)") + "\n\n" + (
            backlog.read_text(encoding="utf-8") if backlog.is_file() else "(no backlog yet)")
        self.push_screen(ReviewScreen(f"Project {ref}", body, review=False))

    def _next_text(self, ref: str | None) -> str:
        if not ref:
            return "Choose a project first: p."
        row = next_story(ref, db_path=self.db_path)
        if row is None:
            return f"No approved story left in the {ref} backlog."
        # Marked started the moment its run exists, and run from its base_commit.
        outcome = run_backlog_story(ref, row, self._clarified(ref, row["request"]), db_path=self.db_path)
        return f"Story {row['title']!r}: run #{outcome.run_id} {outcome.status}."

    def _menu_story(self) -> None:
        ref = self._project()
        if ref:
            self._prompt(f"New story for {ref}", "what should be built",
                         lambda request: self._job("Story", self._story_text, ref, request))

    def _story_text(self, ref: str, request: str) -> str:
        outcome = run_project_pipeline(ref, self._clarified(ref, request), db_path=self.db_path)
        return f"Run #{outcome.run_id} {outcome.status}."

    def _clarified(self, ref: str, request: str) -> str:
        # Same order as `factory next`: the story-level interview first, when a brief exists.
        if has_brief(ref, db_path=self.db_path):
            request = run_story_interview(ref, request, db_path=self.db_path, ask=self._ask)
        return request

    def _menu_run(self, verb: str) -> None:
        fn = retry_run if verb == "retry" else replay_run

        def go(value: str) -> None:
            number = value.strip().lstrip("#")
            if not number.isdigit():
                self._say("A run number, e.g. 12.", "warn")
                return
            self._job(verb.title(), lambda: (
                f"Run #{(o := fn(int(number), db_path=self.db_path)).run_id} {o.status}."))
        self._prompt(f"{verb.title()} which run?", "run number, e.g. 12", go)

    @staticmethod
    def _doctor_text(offline: bool) -> str:
        report = run_doctor(offline=offline)
        icon = {"ok": "✅", "fail": "❌", "warn": "⚠️", "skip": "⏭"}
        lines = [f"{icon.get(c.status, c.status)} {c.name}  {c.detail}" for c in report.checks]
        lines.append("Ready." if report.passed else "A blocking check failed.")
        return "\n".join(lines)

    @staticmethod
    def _evals_text() -> str:
        from factory.selftest import evals
        return evals.render_markdown(evals.run_all())

    @staticmethod
    def _simulate_text() -> str:
        from factory.selftest.simulate import render_markdown, simulate_all
        return render_markdown(simulate_all())

    def _metrics_text(self) -> str:
        from factory.evidence.metrics import compute, render_markdown
        return render_markdown(compute(self.db_path))

    @staticmethod
    def _tiers_text() -> str:
        from factory.agent_config import tiers
        from factory.agent_config.settings import settings
        lines = [f"Runner: {settings().runner.agents}"]
        for agent, tier in sorted(tiers.AGENT_TIERS.items()):
            lines.append(f"{agent:16} {tier:9} {tiers.model_for_tier(tier)}")
        return "\n".join(lines)


def run_board_tui(db_path: Path) -> None:
    FactoryBoard(db_path).run()
