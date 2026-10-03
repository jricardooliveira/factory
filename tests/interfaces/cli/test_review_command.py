"""`factory review <id>`: the checkpoint decision screen."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from factory.interfaces.render import output
from factory.state import db
from factory.workspace import layout

SPEC = {"title": "Bookmark search", "problem": "cannot find", "why": "x",
        "acceptance_criteria": ["title search works"],
        "tasks": [{"id": "T-1", "title": "search", "purpose": "q"}]}
ARCH = {"architecture_notes": "extend the repository", "modules_affected": ["src/search.py"]}
CODER = {"verdict": "complete", "code_blocks": [{"path": "src/search.py", "content": "x = 1\n"}]}
TESTER = {"overall": "pass", "qa_verdict": "pass", "security_verdict": "warn",
          "performance_verdict": "pass", "highest_severity": "low",
          "ac_coverage": ["title search works"], "summary": "Search behaves as specified.",
          "security_findings": ["query is not length-limited"]}
RELEASE = {"verdict": "pass", "summary": "Bookmarks can be searched by title.",
           "how_to_verify": ["call s() and expect a list"],
           "rollback_notes": "revert the commit"}
BOUNDARY = {"overall": "warn", "tenant": {"verdict": "pass"},
            "authorization": {"verdict": "warn", "findings": ["admin route unguarded"]},
            "api_contract": {"verdict": "not_applicable"}, "security": {"verdict": "pass"},
            "required_changes": ["guard the admin route"]}


def _review(run_id: int, *extra: str) -> str:
    from factory.interfaces.cli.main import main

    with output.console.capture() as captured, \
            patch("sys.argv", ["factory", "review", str(run_id), *extra]):
        main()
    return " ".join(captured.get().split())


def _seed(stage: str, agents: list[tuple[str, dict]], gates: list[tuple]) -> int:
    path = layout.db_path()
    db.init_db(path)
    with db.get_db(path) as conn:
        db.create_story(conn, "US-0001", "Bookmark search", "search my bookmarks")
        rid = db.start_run(conn, "US-0001")
        for agent, out in agents:
            db.log_agent(conn, rid, agent, "PROMPT-PREVIEW-TEXT", json.dumps(out),
                         verdict=out.get("verdict") or out.get("overall") or "pass")
        for name, passed, reason, questions in gates:
            db.log_gate(conn, rid, name, passed, reason,
                        needs_human=bool(questions), human_questions=questions)
        db.update_run_stage(conn, rid, stage)
        db.finish_run(conn, rid, "waiting_human")
    return rid


class ReviewAtReleaseCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rid = _seed(
            "gate-release-human",
            [("spec-agent", SPEC), ("architect-agent", ARCH), ("coder-agent", CODER),
             ("tester-agent", TESTER), ("release-agent", RELEASE)],
            [("gate-1-spec", True, "ok", None), ("gate-2-architect", True, "ok", None),
             ("gate-build", True, "[T-1] py_compile:pass", None),
             ("gate-test", True, "passed", None),
             ("gate-release", False, "NOT READY", "Sign off the release?")],
        )
        self.text = _review(self.rid)

    def test_trust_package_names_its_gaps(self) -> None:
        self.assertIn("Trust Package", self.text)
        self.assertIn("Tests were never executed", self.text)

    def test_the_parking_question_and_the_commands_are_shown(self) -> None:
        self.assertIn("Sign off the release?", self.text)
        self.assertIn(f"factory approve {self.rid}", self.text)
        self.assertIn(f'factory reject {self.rid} "<feedback>"', self.text)

    def test_release_and_tester_outputs_are_rendered(self) -> None:
        self.assertIn("Bookmarks can be searched by title.", self.text)
        self.assertIn("call s() and expect a list", self.text)
        self.assertIn("revert the commit", self.text)
        self.assertIn("Search behaves as specified.", self.text)
        self.assertIn("query is not length-limited", self.text)

    def test_prompt_preview_only_with_raw(self) -> None:
        self.assertNotIn("PROMPT-PREVIEW-TEXT", self.text)
        self.assertIn("PROMPT-PREVIEW-TEXT", _review(self.rid, "--raw"))


class ReviewAtArchitectureCheckpointTests(unittest.TestCase):
    def test_question_shown_and_no_trust_package_before_any_code(self) -> None:
        rid = _seed(
            "gate-2-architect",
            [("spec-agent", SPEC), ("architect-agent", ARCH), ("boundary-agent", BOUNDARY)],
            [("gate-1-spec", True, "ok", None),
             ("gate-2-architect", True, "needs human", "Approve breaking change?")],
        )
        text = _review(rid)
        self.assertIn("Approve breaking change?", text)
        self.assertIn(f"factory approve {rid}", text)
        self.assertNotIn("Trust Package", text)
        # boundary-agent's sub-verdicts
        self.assertIn("admin route unguarded", text)
        self.assertIn("guard the admin route", text)



class QueueHintTests(unittest.TestCase):
    """`factory queue` suggests the same next move as `factory status`."""

    def _failed(self, *, project: bool) -> int:
        from factory.workspace.projects import create_project

        path = layout.db_path()
        db.init_db(path)
        pid = create_project(path, slug="habits")["id"] if project else None
        with db.get_db(path) as conn:
            db.create_story(conn, "US-0001", "Streaks", "track streaks", project_id=pid)
            rid = db.start_run(conn, "US-0001", project_id=pid)
            db.finish_run(conn, rid, "failed", error="gate-build failed\npy_compile: boom")
        return rid

    def _queue(self) -> str:
        from factory.interfaces.cli.main import main

        with output.console.capture() as captured, patch("sys.argv", ["factory", "queue"]):
            main()
        return " ".join(captured.get().split())

    def test_a_failed_project_run_says_dismiss_then_next(self) -> None:
        rid = self._failed(project=True)
        text = self._queue()
        self.assertIn(f"factory review {rid}", text)
        self.assertIn(f"factory dismiss {rid} then factory next habits", text)
        self.assertNotIn("factory retry", text)  # nothing answered: retry would refuse
        self.assertNotIn("py_compile: boom", text)  # one line per run

    def test_an_ad_hoc_run_has_no_next_to_suggest(self) -> None:
        rid = self._failed(project=False)
        text = self._queue()
        self.assertIn(f"factory dismiss {rid}", text)
        self.assertNotIn("factory next", text)


if __name__ == "__main__":
    unittest.main()
