"""Checkpoint 1 — spec sign-off, and the orphaned-run bug at gate-1.

Two defects lived in `node_gate_1`:

1. A gate-1 failure returned `status: "failed"` in the graph state but never called
   `finish_run`, so `pipeline_runs.status` stayed `'running'` with a NULL error
   forever. The operator was never told the story had been rejected: the run does
   not show up in `factory queue`, and only `factory reconcile --older-than 3600`
   eventually mops it up as a dead process — misattributing a policy decision to a
   crash.

2. `spec.questions` — the ONE place where the playbook's Stage-1 clarifying-question
   round-trip belongs, and the one thing only a human can answer — was appended to
   `failures` and killed the run. EFFECTIVENESS.md §2 and REVIEW_QUEUE.md both
   specify Checkpoint 1 (spec sign-off) for exactly this; it just wasn't built.

The pure gate decisions are pinned in tests/domain/test_gate_after_spec.py and the
threshold-term detection in tests/domain/test_ambiguity.py; this suite keeps what
needs the graph or the DB.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory.domain import gates
from factory.domain.contracts import SpecOutput
from factory.pipeline import compile_pipeline
from factory.state import db


def _spec(**overrides) -> SpecOutput:
    data = {
        "title": "Bookmark search",
        "problem": "no way to find a bookmark",
        "why": "the list is unusable past 50 entries",
        "acceptance_criteria": ["search matches on title", "results are paginated"],
        "tasks": [{"id": "T-1", "title": "add search", "purpose": "p",
                   "scope": ["src/bookmarks/"], "completion_evidence": "test passes"}],
        "verdict": "pass",
        "questions": [],
    }
    data.update(overrides)
    return SpecOutput.model_validate(data)


class Gate1PersistenceTests(unittest.TestCase):
    """The DB must reflect what the gate decided — the operator reads the DB."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "f.db"
        self.cwd = self.root / "repo"
        self.cwd.mkdir()
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _drive(self, spec_output: dict) -> tuple[int, dict, dict]:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0001", "Pending", "add search")
            orig = db.start_run(conn, "US-0001")
            db.log_agent(conn, orig, "spec-agent", "in", json.dumps(spec_output), verdict="pass")
            new = db.start_run(conn, "US-0001")
        state = {"request": "add search", "story_id": "US-0001", "run_id": new,
                 "db_path": str(self.db_path), "opencode_cwd": str(self.cwd),
                 "replay_run_id": orig}
        for event in compile_pipeline().stream(state):
            pass
        with db.get_db(self.db_path) as conn:
            run = dict(conn.execute(
                "SELECT status, finished_at, error, current_stage FROM pipeline_runs WHERE id = ?",
                (new,)).fetchone())
            story = dict(conn.execute(
                "SELECT status FROM stories WHERE id = 'US-0001'").fetchone())
            gate_rows = [dict(g) for g in db.get_run_gates(conn, new)]
        return new, run, {"story": story, "gates": gate_rows}

    def test_structural_failure_finishes_the_run_instead_of_orphaning_it(self) -> None:
        spec = _spec(acceptance_criteria=["only one"]).model_dump()
        _rid, run, extra = self._drive(spec)
        self.assertEqual(run["status"], "failed",
                         "a gate-1 rejection must not leave the run stuck 'running'")
        self.assertIsNotNone(run["finished_at"])
        self.assertTrue(run["error"], "the reason for the rejection must be recorded")
        self.assertIn("acceptance criteria", run["error"])
        self.assertEqual(extra["story"]["status"], "failed")

    def test_open_questions_park_the_run_as_waiting_human(self) -> None:
        spec = _spec(questions=["Which auth model — JWT or session?"]).model_dump()
        rid, run, extra = self._drive(spec)
        self.assertEqual(run["status"], "waiting_human")
        self.assertEqual(run["current_stage"], "gate-1-human")
        gate = next(g for g in extra["gates"] if g["gate_name"] == "gate-1-spec")
        self.assertTrue(gate["needs_human"])
        self.assertIn("JWT or session", gate["human_questions"])
        # And it must be findable by the operator, which is the whole point.
        with db.get_db(self.db_path) as conn:
            parked = [r["id"] for r in db.get_runs_by_status(conn, ["waiting_human"])]
        self.assertIn(rid, parked)

    def test_a_parked_spec_never_reaches_the_architect(self) -> None:
        spec = _spec(questions=["which db?"]).model_dump()
        _rid, _run, extra = self._drive(spec)
        self.assertEqual(
            [g["gate_name"] for g in extra["gates"]], ["gate-1-spec"],
            "the line must stop at checkpoint 1, not run the architect anyway",
        )


class SpecPromptTests(unittest.TestCase):
    """The spec-agent used to receive the bare request string as its entire prompt,
    so it wrote acceptance criteria blind to the project's own hard constraints —
    and a criterion violating `forbidden` could only be dropped one stage later."""

    def test_project_constraints_reach_the_spec_agent(self) -> None:
        from factory.pipeline import build_spec_prompt

        prompt = build_spec_prompt({
            "request": "add a live feed",
            "project_spec": "## Project Specification\n\n- ❌ no WebSockets",
        })
        self.assertIn("add a live feed", prompt)
        self.assertIn("no WebSockets", prompt)

    def test_operator_feedback_reaches_a_rejected_spec_rerun(self) -> None:
        from factory.pipeline import build_spec_prompt

        prompt = build_spec_prompt({
            "request": "add a live feed",
            "triggered_by": "spec-rejected",
            "prior_findings": ["Use polling, not push. And drop the admin screen."],
        })
        self.assertIn("polling, not push", prompt)
        self.assertIn("add a live feed", prompt)

    def test_bare_request_still_works_with_no_context(self) -> None:
        from factory.pipeline import build_spec_prompt

        self.assertIn("do the thing", build_spec_prompt({"request": "do the thing"}))


if __name__ == "__main__":
    unittest.main()


class SettledTermsWiringTests(unittest.TestCase):
    """The gate must actually consult the project's committed memory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "f.db"
        self.cwd = self.root / "repo"
        self.cwd.mkdir()
        self.project = self.root / "PROJ-010"
        (self.project / "docs" / "architecture" / "adr").mkdir(parents=True)
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _drive(self, *, with_adr: bool, approved: bool = True) -> str:
        spec = _spec(acceptance_criteria=[
            "The dashboard shows the number of overdue HIGH tickets",
            "Counts are correct for an empty database",
        ]).model_dump()
        if with_adr:
            status = ("approved by operator" if approved
                      else "proposed (pending architecture sign-off)")
            (self.project / "docs" / "architecture" / "adr" / "ADR-US-0011-sla.md").write_text(
                f"# ADR-US-0011: Ticket SLA\n\n- Status: {status}\n\n## Decision\n"
                "A HIGH ticket is OVERDUE when status != CLOSED and createdAt is "
                "more than 24 hours ago.\n",
                encoding="utf-8",
            )
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0013", "Dashboard", "show the workload")
            orig = db.start_run(conn, "US-0013")
            db.log_agent(conn, orig, "spec-agent", "in", json.dumps(spec), verdict="pass")
            new = db.start_run(conn, "US-0013")
        state = {"request": "dashboard", "story_id": "US-0013", "run_id": new,
                 "db_path": str(self.db_path), "opencode_cwd": str(self.cwd),
                 "project_dir": str(self.project), "replay_run_id": orig}
        for _event in compile_pipeline().stream(state):
            pass
        with db.get_db(self.db_path) as conn:
            return conn.execute(
                "SELECT status FROM pipeline_runs WHERE id = ?", (new,)
            ).fetchone()["status"]

    def test_an_undefined_threshold_parks_when_nothing_has_settled_it(self) -> None:
        self.assertEqual(self._drive(with_adr=False), "waiting_human")

    def test_an_APPROVED_adr_defining_the_term_lets_the_story_through(self) -> None:
        self.assertNotEqual(
            self._drive(with_adr=True, approved=True), "waiting_human",
            "a term settled by an APPROVED ADR must not be re-litigated",
        )

    def test_an_unapproved_adr_does_not_let_the_story_through(self) -> None:
        """Every ADR the factory writes is stamped 'proposed'. Trusting those let
        one run's invented threshold excuse the next run's — see
        OnlyOperatorAuthoredContextSettlesTests."""
        self.assertEqual(
            self._drive(with_adr=True, approved=False), "waiting_human",
            "an unapproved ADR is a proposal, not a decision",
        )


class OnlyOperatorAuthoredContextSettlesTests(unittest.TestCase):
    """A threshold is settled only by something a HUMAN wrote.

    The "don't re-litigate settled decisions" exemption originally read the whole
    project memory block, prior ADRs included. But ADRs are written by the
    architect-agent and `evidence.adr.render_adr` stamps every one of them
    `Status: proposed (pending architecture sign-off)` — none is ever approved.

    Observed live across three runs of the same request: run 17 asked four
    clarifying questions; run 18's ARCHITECT silently chose "at least 24 hours"
    and wrote it into an ADR; run 19's spec-agent then sailed through the new
    gate — because that unapproved ADR had made "overdue" look settled — and
    chose **48 hours**, contradicting run 18. The exemption laundered an invented
    number into a decision.

    Only operator-authored context counts: the project spec and PROJECT_RULES.md.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "PROJ-010"
        (self.project / "docs" / "architecture" / "adr").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _adr(self, text: str) -> None:
        (self.project / "docs" / "architecture" / "adr" / "ADR-US-0018-x.md").write_text(
            text, encoding="utf-8"
        )

    def test_an_unapproved_adr_does_NOT_settle_a_threshold(self) -> None:
        from factory.pipeline import settled_threshold_terms

        self._adr("# ADR-US-0018: Overdue\n\n- Status: proposed (pending architecture "
                  "sign-off)\n\n## Decision\nA ticket is OVERDUE after 24 hours.\n")
        settled = settled_threshold_terms({"project_dir": str(self.project),
                                           "story_id": "US-0019"})
        self.assertNotIn(
            "overdue", settled,
            "an agent-invented threshold in an unapproved ADR must not settle it",
        )

    def test_project_rules_written_by_the_operator_DO_settle_it(self) -> None:
        from factory.pipeline import settled_threshold_terms

        (self.project / "PROJECT_RULES.md").write_text(
            "# Rules\n\nA HIGH ticket is OVERDUE when not CLOSED and created more "
            "than 24 hours ago.\n", encoding="utf-8")
        settled = settled_threshold_terms({"project_dir": str(self.project),
                                           "story_id": "US-0019"})
        self.assertIn("overdue", settled)

    def test_the_project_spec_also_settles_it(self) -> None:
        from factory.pipeline import settled_threshold_terms

        settled = settled_threshold_terms({
            "project_spec": "### Non-Functional Requirements\n- A HIGH ticket is "
                            "overdue after 24 hours without closure",
            "story_id": "US-0019",
        })
        self.assertIn("overdue", settled)

    def test_an_approved_adr_settles_it(self) -> None:
        """Once ADR approval is recorded, the decision genuinely IS settled."""
        from factory.pipeline import settled_threshold_terms

        self._adr("# ADR-US-0018: Overdue\n\n- Status: approved by operator\n\n"
                  "## Decision\nA ticket is OVERDUE after 24 hours.\n")
        settled = settled_threshold_terms({"project_dir": str(self.project),
                                           "story_id": "US-0019"})
        self.assertIn("overdue", settled)

    def test_no_project_context_settles_nothing(self) -> None:
        from factory.pipeline import settled_threshold_terms

        self.assertEqual(settled_threshold_terms({"story_id": "US-0001"}), frozenset())


class OperatorAnswerSettlesTermTests(unittest.TestCase):
    """Answering a checkpoint must SETTLE the question, not restart it.

    Observed live: the operator answered "overdue means: priority HIGH, not
    closed, and older than 24 hours". The spec-agent used it perfectly — the
    re-specified story states the rule exactly. The gate then parked the story
    AGAIN, because `settled_threshold_terms` only read project files and never
    the operator's own recorded answers. Answer, get re-asked, forever.

    For a factory whose metric is "trust per interruption", an interruption that
    cannot be discharged is the worst possible defect: it spends the scarcest
    resource and buys nothing.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "f.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_with_answer(self, answer: str | None) -> int:
        with db.get_db(self.db_path) as conn:
            db.create_story(conn, "US-0020", "Overdue", "highlight overdue tickets")
            rid = db.start_run(conn, "US-0020")
            gid = db.log_gate(conn, rid, "gate-1-spec", True, "parked",
                              needs_human=True, human_questions="what is overdue?")
            if answer:
                db.respond_to_gate(conn, gid, answer)
        return rid

    def test_the_operators_answer_settles_the_term(self) -> None:
        from factory.pipeline import settled_threshold_terms

        rid = self._run_with_answer(
            "REJECTED: overdue means: priority HIGH, not closed, and older than 24 hours")
        settled = settled_threshold_terms(
            {"story_id": "US-0020", "run_id": rid, "db_path": str(self.db_path)}
        )
        self.assertIn(
            "overdue", settled,
            "a question the operator has already answered must not be re-asked",
        )

    def test_an_unanswered_checkpoint_settles_nothing(self) -> None:
        from factory.pipeline import settled_threshold_terms

        rid = self._run_with_answer(None)
        self.assertEqual(
            settled_threshold_terms(
                {"story_id": "US-0020", "run_id": rid, "db_path": str(self.db_path)}
            ),
            frozenset(),
        )

    def test_an_answer_about_something_else_does_not_settle_this_term(self) -> None:
        from factory.pipeline import settled_threshold_terms

        rid = self._run_with_answer("APPROVED: the design looks fine to me")
        self.assertNotIn(
            "overdue",
            settled_threshold_terms(
                {"story_id": "US-0020", "run_id": rid, "db_path": str(self.db_path)}
            ),
        )

    def test_the_gate_stops_re_asking_once_answered(self) -> None:
        """End to end: the same story that parked must now proceed."""
        from factory.pipeline import settled_threshold_terms

        rid = self._run_with_answer(
            "REJECTED: overdue means: priority HIGH, not closed, and older than 24 hours")
        settled = settled_threshold_terms(
            {"story_id": "US-0020", "run_id": rid, "db_path": str(self.db_path)}
        )
        result = gates.gate_after_spec(
            _spec(acceptance_criteria=[
                "A ticket is overdue only when priority is HIGH, status is not CLOSED, "
                "and created_at is older than 24 hours",
                "Overdue tickets get a distinct visual treatment",
            ]),
            defined_terms=settled,
            request="I want overdue high-priority tickets highlighted",
        )
        self.assertTrue(result.passed)
        self.assertFalse(
            result.needs_human,
            "the operator answered this exact question — asking again is a loop",
        )
