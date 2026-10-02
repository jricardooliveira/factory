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
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory import gates
from factory.models import SpecOutput
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


class GateAfterSpecTests(unittest.TestCase):
    def test_clean_spec_passes_without_a_checkpoint(self) -> None:
        result = gates.gate_after_spec(_spec())
        self.assertTrue(result.passed)
        self.assertFalse(result.needs_human)

    def test_open_questions_park_for_the_operator_instead_of_failing(self) -> None:
        result = gates.gate_after_spec(_spec(questions=["Which auth model — JWT or session?"]))
        self.assertTrue(
            result.passed,
            "an otherwise well-formed story with questions is not a malformed story",
        )
        self.assertTrue(result.needs_human)
        self.assertTrue(result.human_questions)
        self.assertIn("JWT or session", "\n".join(result.human_questions))

    def test_structural_failure_still_fails_even_with_questions(self) -> None:
        """A malformed story must not be laundered into a checkpoint: there is
        nothing for the operator to sign off on."""
        result = gates.gate_after_spec(_spec(acceptance_criteria=["only one"],
                                             questions=["and also, which db?"]))
        self.assertFalse(result.passed)
        self.assertFalse(result.needs_human)

    def test_scope_explosion_still_fails(self) -> None:
        many = [{"id": f"T-{i}", "title": "t", "purpose": "p"} for i in range(1, 9)]
        self.assertFalse(gates.gate_after_spec(_spec(tasks=many)).passed)


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


class AgentAmbiguityContractTests(unittest.TestCase):
    """The gate must honour the contract the agent was actually GIVEN.

    `.opencode/agents/spec-agent.md` instructs: "If the request is ambiguous, set
    `verdict: fail` and put your questions in a `questions` array." But
    `gate_after_spec` checked `verdict != "pass"` first and rejected the run as
    structurally malformed, so the Checkpoint-1 park — which only fired when the
    verdict PASSED — could never trigger for the one case it exists to serve.

    Found by a live run of the challenge's Story 10, where the spec-agent
    correctly refused to invent a definition of "overdue" and asked four precise
    product questions. The pipeline discarded all of it and reported
    "Spec verdict is 'fail', not 'pass'". The replay probe missed this because
    the case was hand-authored with `verdict: "pass"` — it encoded an assumption
    about the agent instead of the agent's real contract.
    """

    def test_verdict_fail_WITH_questions_is_a_checkpoint_not_a_rejection(self) -> None:
        result = gates.gate_after_spec(_spec(
            verdict="fail",
            questions=[
                "What exact rule defines an overdue ticket: age since creation, "
                "time since last update, due date field, or SLA by priority?",
                "Where must the highlighting appear?",
            ],
        ))
        self.assertTrue(
            result.passed,
            "an agent following its own ambiguity contract must not be treated as "
            "having produced a malformed story",
        )
        self.assertTrue(result.needs_human)
        self.assertIn("overdue", "\n".join(result.human_questions))

    def test_verdict_blocked_with_questions_also_parks(self) -> None:
        result = gates.gate_after_spec(_spec(verdict="blocked", questions=["which db?"]))
        self.assertTrue(result.passed)
        self.assertTrue(result.needs_human)

    def test_verdict_fail_WITHOUT_questions_is_still_a_rejection(self) -> None:
        """A bare 'fail' with nothing to ask is a genuine refusal, not a
        checkpoint — there is nothing for the operator to answer."""
        result = gates.gate_after_spec(_spec(verdict="fail", questions=[]))
        self.assertFalse(result.passed)
        self.assertFalse(result.needs_human)
        self.assertIn("verdict", result.reason.lower())

    def test_a_structurally_broken_story_is_rejected_even_with_questions(self) -> None:
        """Ambiguity does not excuse a malformed story: with one acceptance
        criterion there is nothing coherent to sign off on."""
        result = gates.gate_after_spec(_spec(
            verdict="fail", acceptance_criteria=["only one"], questions=["which db?"],
        ))
        self.assertFalse(result.passed)
        self.assertFalse(result.needs_human)

    def test_the_real_story_10_output_parks(self) -> None:
        """Verbatim shape of what the spec-agent returned on the live run."""
        result = gates.gate_after_spec(SpecOutput.model_validate({
            "title": "Highlight overdue high-priority tickets",
            "type": "feature",
            "problem": "Support managers have no explicit way to identify "
                       "high-priority tickets that are overdue.",
            "why": "important cases are forgotten",
            "acceptance_criteria": [
                "The system identifies high-priority tickets as overdue according "
                "to a clearly defined overdue rule.",
                "Overdue high-priority tickets are highlighted in the support agent "
                "ticket list.",
            ],
            "non_goals": ["Authentication or user management"],
            "tasks": [
                {"id": "T-0001", "title": "Define overdue rule and API contract",
                 "purpose": "p", "scope": [], "completion_evidence": "e"},
                {"id": "T-0002", "title": "Expose overdue flag in ticket listing",
                 "purpose": "p", "scope": [], "completion_evidence": "e"},
                {"id": "T-0003", "title": "Render overdue highlight in frontend",
                 "purpose": "p", "scope": [], "completion_evidence": "e"},
            ],
            "verdict": "fail",
            "questions": [
                "What exact rule defines an overdue ticket?",
                "Where must the highlighting appear?",
                "What visual treatment counts as highlighted?",
                "Should overdue status be filterable or searchable?",
            ],
        }))
        self.assertTrue(result.passed, result.reason)
        self.assertTrue(result.needs_human)
        self.assertIn("4 open question", result.reason)
        # BOTH triggers fire on the real output: the agent asked, and the
        # deterministic check independently found "overdue" carrying no number.
        # That redundancy is the point — the deterministic half holds when the
        # agent's half does not (which a live re-run showed happens).
        self.assertEqual(len(result.human_questions), 2)
        joined = "\n".join(result.human_questions)
        self.assertIn("What exact rule defines an overdue ticket?", joined)
        self.assertIn("UNAUTHORISED THRESHOLD", joined)
        self.assertIn("undefined threshold", result.reason)


class UnboundCriteriaTests(unittest.TestCase):
    """Deterministically catch a criterion that requires inventing a threshold.

    The live test showed ambiguity detection is a coin flip: the same Story 10
    request produced four precise questions on one run and `verdict: pass` on the
    next. For a factory whose premise is "policy is deterministic Python, never
    delegated to an LLM", whether an under-specified requirement gets flagged is
    the wrong thing to leave to the model's mood.

    The check is deliberately NARROW. It looks only for terms that oblige the
    implementation to invent a NUMBER it was never given — the class of silent
    business-rule invention Story 10 exists to test. Quality adjectives
    ("appropriate tests", "reasonable error handling") are excluded on purpose:
    the challenge uses "appropriate" in eight of its fifteen stories, so flagging
    those would park nearly every story and train the operator to click through —
    the crying-wolf failure this whole check exists to avoid.
    """

    def test_the_real_story_10_criterion_is_flagged(self) -> None:
        found = gates.unbound_criteria([
            "Overdue high-priority tickets are highlighted in the ticket list",
        ])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1], "overdue")

    def test_a_criterion_carrying_its_threshold_is_not_flagged(self) -> None:
        """Story 11 supplies the definition, so it must sail through."""
        self.assertEqual(
            gates.unbound_criteria([
                "A HIGH-priority ticket is overdue when status != CLOSED and "
                "createdAt is more than 24 hours ago",
            ]),
            [],
        )

    def test_spelled_out_numbers_and_units_count_as_bound(self) -> None:
        for criterion in (
            "tickets with no update for seven days are marked stale",
            "a search returning more than fifty results is paginated",
            "requests slower than 200ms are logged",
        ):
            with self.subTest(criterion=criterion):
                self.assertEqual(gates.unbound_criteria([criterion]), [])

    def test_quality_adjectives_are_deliberately_not_flagged(self) -> None:
        """These are review-policy concerns (REVIEW.md + the tester), not
        unstated business rules — and the challenge is full of them."""
        self.assertEqual(
            gates.unbound_criteria([
                "Appropriate unit tests exist",
                "Appropriate database/API integration tests exist",
                "API errors use a consistent response format",
                "Invalid input returns an appropriate HTTP 400 response",
                "Frontend provides a loading state",
                "Empty ticket collections are handled correctly",
            ]),
            [],
        )

    def test_the_challenge_story_1_criteria_all_pass_cleanly(self) -> None:
        """A well-specified story must never trip this check."""
        self.assertEqual(
            gates.unbound_criteria([
                "Title is mandatory",
                "Description is mandatory",
                "Priority must be one of LOW, MEDIUM, HIGH",
                "Status defaults to OPEN",
                "Ticket is persisted in SQLite",
                "Invalid input returns an appropriate HTTP 400 response",
            ]),
            [],
        )

    def test_a_term_already_settled_in_project_memory_is_not_re_litigated(self) -> None:
        """Once Story 11 defines 'overdue' in a committed ADR, Story 13's
        dashboard criterion must not park again — the factory does not
        re-litigate settled decisions (EFFECTIVENESS §7)."""
        self.assertEqual(
            gates.unbound_criteria(
                ["The dashboard shows the number of overdue HIGH or URGENT tickets"],
                defined_terms=frozenset({"overdue"}),
            ),
            [],
        )

    def test_defined_terms_are_extracted_from_committed_project_context(self) -> None:
        from factory.gates import defined_threshold_terms

        context = (
            "## Prior Architecture Decisions\n\n"
            "ADR-US-0011: a HIGH ticket is OVERDUE when it is not CLOSED and was "
            "created more than 24 hours ago.\n"
        )
        self.assertIn("overdue", defined_threshold_terms(context))
        self.assertNotIn("stale", defined_threshold_terms(context))

    def test_empty_context_defines_nothing(self) -> None:
        from factory.gates import defined_threshold_terms

        self.assertEqual(defined_threshold_terms(""), frozenset())


class UnboundCriteriaGateTests(unittest.TestCase):
    """An unbound criterion PARKS the story — it is a question, not a defect."""

    def test_gate_parks_and_names_the_undefined_term(self) -> None:
        result = gates.gate_after_spec(_spec(acceptance_criteria=[
            "Overdue high-priority tickets are highlighted in the list",
            "The highlight is visible on the dashboard",
        ]))
        self.assertTrue(result.passed, "an under-specified story is not malformed")
        self.assertTrue(result.needs_human)
        joined = "\n".join(result.human_questions)
        self.assertIn("overdue", joined.lower())
        self.assertIn("Overdue high-priority tickets", joined)

    def test_a_well_specified_story_still_needs_no_human(self) -> None:
        result = gates.gate_after_spec(_spec())
        self.assertTrue(result.passed)
        self.assertFalse(result.needs_human)

    def test_the_agents_own_questions_and_the_deterministic_check_combine(self) -> None:
        result = gates.gate_after_spec(_spec(
            verdict="fail",
            acceptance_criteria=["Overdue tickets are highlighted", "It looks nice"],
            questions=["Where should the highlight appear?"],
        ))
        self.assertTrue(result.passed)
        self.assertTrue(result.needs_human)
        joined = "\n".join(result.human_questions)
        self.assertIn("Where should the highlight appear?", joined)
        self.assertIn("overdue", joined.lower())

    def test_settled_terms_do_not_park_the_gate(self) -> None:
        result = gates.gate_after_spec(
            _spec(acceptance_criteria=["Overdue tickets are counted on the dashboard",
                                       "Counts are correct for an empty database"]),
            defined_terms=frozenset({"overdue"}),
        )
        self.assertFalse(result.needs_human)

    def test_a_structural_failure_still_wins_over_the_ambiguity_park(self) -> None:
        result = gates.gate_after_spec(_spec(
            acceptance_criteria=["Overdue tickets are highlighted"],  # only 1 AC
        ))
        self.assertFalse(result.passed)
        self.assertFalse(result.needs_human)


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


class UnitMatchingTests(unittest.TestCase):
    """Units must match as WORDS, never as substrings.

    The first implementation tested `unit in criterion.lower()`, so "nu**mb**er"
    matched the megabyte unit "mb" and the criterion was judged to carry a
    threshold it did not have. "ms" is worse: it matches almost any plural ending
    in -ms — "items", "terms", "problems", "forms" — which would have silently
    disabled this check across most real acceptance criteria. A check that
    quietly stops checking is the worst kind.
    """

    def test_a_unit_hidden_inside_another_word_does_not_count_as_bound(self) -> None:
        for criterion in (
            "The dashboard shows the number of overdue HIGH tickets",   # nu(mb)er
            "Overdue items are listed first",                          # ite(ms)
            "Stale search terms are highlighted",                      # ter(ms)
            "Recent problems appear at the top",                       # proble(ms)
        ):
            with self.subTest(criterion=criterion):
                found = gates.unbound_criteria([criterion])
                self.assertEqual(
                    len(found), 1,
                    f"a unit hidden inside a word must not bind: {criterion}",
                )

    def test_a_real_unit_as_its_own_word_does_count_as_bound(self) -> None:
        for criterion in (
            "tickets with no update for seven days are stale",
            "requests slower than 200 ms are logged",
            "uploads larger than 5 mb are rejected",
            "a ticket is overdue after twenty four hours",
        ):
            with self.subTest(criterion=criterion):
                self.assertEqual(gates.unbound_criteria([criterion]), [])

    def test_plural_units_bind_too(self) -> None:
        self.assertEqual(
            gates.unbound_criteria(["a ticket is overdue after two weeks"]), []
        )


class OnlyOperatorAuthoredContextSettlesTests(unittest.TestCase):
    """A threshold is settled only by something a HUMAN wrote.

    The "don't re-litigate settled decisions" exemption originally read the whole
    project memory block, prior ADRs included. But ADRs are written by the
    architect-agent and `memory.render_adr` stamps every one of them
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


class InventedThresholdTests(unittest.TestCase):
    """An INVENTED threshold must park too, not just a missing one.

    The first version of this check keyed only off the acceptance criteria, so a
    criterion carrying a number was "bound" and sailed through — even when the
    request never supplied that number. Observed live: the request said only
    "overdue high-priority tickets", and one run wrote "created_at is older than
    48 hours" while an earlier run's architect had chosen 24. Both are
    undocumented business rules, both unapproved; writing the number down makes
    the invention visible but does not make it authorised.

    So the question is asked of the REQUEST: if the operator used a threshold
    term without a number, the definition needs sign-off however the agent
    resolves it.
    """

    REQUEST = ("As a support manager, I want overdue high-priority tickets "
               "highlighted so that important cases are not forgotten.")

    def test_a_number_the_request_never_supplied_is_flagged(self) -> None:
        found = gates.unbound_criteria(
            ["A ticket is overdue when priority is HIGH and created_at is older "
             "than 48 hours."],
            request=self.REQUEST,
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1], "overdue")

    def test_a_threshold_the_request_DID_supply_is_not_flagged(self) -> None:
        """Story 11 hands over the definition, so nothing needs approving."""
        self.assertEqual(
            gates.unbound_criteria(
                ["A HIGH ticket is overdue when status != CLOSED and createdAt is "
                 "more than 24 hours ago."],
                request="A HIGH-priority ticket is overdue when status != CLOSED "
                        "and createdAt is more than 24 hours ago.",
            ),
            [],
        )

    def test_an_operator_settled_term_is_not_flagged_even_from_the_request(self) -> None:
        self.assertEqual(
            gates.unbound_criteria(
                ["Overdue tickets are counted on the dashboard"],
                request=self.REQUEST,
                defined_terms=frozenset({"overdue"}),
            ),
            [],
        )

    def test_a_term_absent_from_the_request_is_still_judged_on_the_criterion(self) -> None:
        """The agent introducing a NEW vague qualifier of its own must still be
        caught — the request-based rule widens the net, it does not replace it."""
        found = gates.unbound_criteria(
            ["Stale tickets are archived"],
            request="I want a dashboard showing ticket counts",
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1], "stale")

    def test_the_message_distinguishes_invented_from_missing(self) -> None:
        result = gates.gate_after_spec(
            _spec(acceptance_criteria=[
                "A ticket is overdue when created_at is older than 48 hours.",
                "Overdue tickets are highlighted in the list.",
            ]),
            request=self.REQUEST,
        )
        self.assertTrue(result.needs_human)
        joined = "\n".join(result.human_questions)
        self.assertIn("48 hours", joined)
        self.assertIn("never", joined.lower())

    def test_no_request_falls_back_to_criterion_only_judgement(self) -> None:
        self.assertEqual(
            gates.unbound_criteria(
                ["A ticket is overdue after 24 hours"], request=""
            ),
            [],
        )


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
