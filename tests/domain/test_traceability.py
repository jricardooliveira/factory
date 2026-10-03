"""Tests for deterministic acceptance-criteria traceability."""

from __future__ import annotations

import unittest

from factory.domain.traceability import trace_criteria, unassessed_criteria


class TraceCriteriaTests(unittest.TestCase):
    def test_exact_coverage_marks_covered(self) -> None:
        trace = trace_criteria(
            ["Converts 0C to 32F", "Converts 100C to 212F"],
            ["Converts 0C to 32F", "Converts 100C to 212F"],
            [],
        )
        self.assertTrue(all(e["status"] == "covered" for e in trace))

    def test_paraphrased_claim_still_matches(self) -> None:
        trace = trace_criteria(
            ["search returns matching items"],
            ["search endpoint returns the matching items"],
            [],
        )
        self.assertEqual(trace[0]["status"], "covered")

    def test_criterion_only_in_missing_is_flagged(self) -> None:
        trace = trace_criteria(
            ["search returns matching items"],
            [],
            ["search returns matching items"],
        )
        self.assertEqual(trace[0]["status"], "flagged_missing")

    def test_unmentioned_criterion_is_unassessed(self) -> None:
        trace = trace_criteria(
            ["Converts 0C to 32F", "rejects negative input"],
            ["Converts 0C to 32F"],
            [],
        )
        self.assertEqual(unassessed_criteria(trace), ["rejects negative input"])

    def test_no_criteria_yields_empty(self) -> None:
        self.assertEqual(trace_criteria([], ["x"], []), [])


# Verbatim from the live habits runs (agent_logs: spec-agent + tester-agent of run 6;
# criteria 10-11 and tester rows 61 and 52 of run 5). The tester numbers its claims
# "ACn" by the criterion's position in the story and paraphrases the text.
RUN_6_CRITERIA = [
    "The first page (GET /) shows every habit in its existing order as today's list, each "
    "marked done or not done for the current habit day.",
    "Clicking anywhere on a habit's row once marks it done for today without a full page "
    "reload; clicking the row again unticks it.",
    "A ticked habit stays in the same position in the list and is shown with a clear check "
    "mark and a faded look; an unticked habit has neither.",
    "Each tick is saved as a row in daily_logs in the same single SQLite file as the habits "
    "(amount 1, goal snapshot 1, for that habit and habit day); unticking removes that row. "
    "After building a second app on the same data file (close and reopen), the same habits "
    "are shown as ticked.",
    "The habit day is computed by the server from the laptop's local time with the day ending "
    "at 4:00 am: a tick made at 03:59 is saved for the previous calendar date, and a page "
    "loaded at 04:00 or later shows a fresh list with every habit unticked, while the earlier "
    "day's ticks remain stored.",
    "The client never sends a date and no route accepts one: only the current habit day can "
    "be ticked or unticked.",
    "Ticking is idempotent: sending 'done: true' twice leaves exactly one log row, and sending "
    "'done: false' twice leaves none; neither returns an error.",
    "If a tick or untick cannot be saved (the server answers with a non-200 status or the "
    "request fails, for example the disk is full or the data file is unreadable), the row goes "
    "back to its previous state and the page shows a clear message saying the change was not "
    "saved; no stack trace is shown.",
    "A page left open past 4:00 am keeps showing the previous day until it is reloaded; there "
    "is no automatic refresh timer. If a click on such a stale page gets a response for a "
    "different habit day than the page was rendered for, the page reloads so what is shown "
    "matches what is saved.",
    "A tick request for an unknown habit id returns 404, a request whose Content-Type is not "
    "application/json or whose 'done' value is not a JSON boolean returns 400, and a request "
    "with a foreign Host or Origin header returns 403; none of these changes any data.",
    "The page loads its script only from the app's own local static folder: no external URLs, "
    "CDNs, web fonts or inline event handlers, and the page and script contain no http:// or "
    "https:// strings.",
    "Adding a habit, the name messages, the unreadable-data-file page, GET /health and the "
    "launcher behave exactly as before, and all existing tests still pass (the "
    "no-external-script test is narrowed to allow only the local static script, not removed).",
]
RUN_6_CLAIMS = [
    "AC1 GET / shows every habit in existing order with done/not-done for the current habit "
    "day — test_rows_render_done_and_not_done_markup, test_order_unchanged_after_ticking, "
    "test_list_for_day_order_fields_and_done_flag",
    "AC2 one click on the row ticks without a full reload, a second click unticks — server "
    "side tested (test_tick_then_untick) and the label-wraps-checkbox markup tested; the "
    "click/fetch behaviour in habits.js is verified by reading the diff only (no browser test "
    "tooling in the stack, manual check per T-0004)",
    "AC3 ticked habit keeps its position and has a check mark and faded look — position and "
    "checked attribute tested; the look is CSS tied to input:checked (opacity 0.55, ::before "
    "check mark), verified by reading the diff",
    "AC4 tick saved as a daily_logs row (amount 1, goal 1) in the same file, untick removes "
    "it, ticks survive a second app — test_set_done_stores_amount_1_goal_1, "
    "test_tick_then_untick, test_ticks_survive_second_app_on_same_file",
    "AC5 4:00 am day boundary — test_services habit_day tests (03:59, 04:00, month and year "
    "rollover), test_tick_at_0359_is_saved_for_previous_date, "
    "test_new_habit_day_starts_at_four_and_keeps_old_rows",
    "AC6 client never sends a date and no route accepts one — test_extra_date_key_is_ignored; "
    "the route reads only 'done' and habits.js sends only {done}",
    "AC7 idempotent tick and untick — test_repeated_tick_and_untick_are_idempotent, "
    "test_repeat_tick_leaves_one_row, test_repeat_untick_leaves_none",
    "AC8 failed save reverts the row and shows a 'not saved' message with no stack trace — "
    "server side tested (test_write_error_gives_non_200_without_traceback, "
    "test_tick_in_data_error_mode_gets_503_and_writes_nothing); the revert and textContent "
    "message in habits.js are verified by reading the diff only",
    "AC9 no automatic refresh; a stale page reloads on day mismatch — absence of "
    "setTimeout/setInterval tested, response 'day' and data-day tested; the location.reload() "
    "comparison in habits.js is verified by reading the diff only",
    "AC10 404 unknown id, 400 wrong Content-Type or non-boolean 'done', 403 foreign "
    "Host/Origin, no data changed — test_unknown_habit_gets_404_and_saves_nothing, "
    "test_wrong_content_type_gets_400, test_non_boolean_done_gets_400 (parametrized), "
    "test_malformed_json_gets_400, test_tick_with_foreign_host_gets_403_and_saves_nothing, "
    "test_tick_with_foreign_origin_gets_403_and_saves_nothing",
    "AC11 script only from local static, no external URLs, no inline handlers, no http(s) "
    "strings — narrowed test_index_has_no_external_urls_or_scripts, "
    "test_script_has_no_external_urls_and_no_inline_handlers",
    "AC12 add habit, name messages, unreadable-file page, /health and launcher unchanged; "
    "existing tests pass — existing suites kept, test_adding_a_habit_still_works_after_ticking, "
    "test_error_mode_has_no_list_form_or_script, build gate pytest pass; launcher.py, run.py, "
    "db.py, the migration and conftest are not in the diff",
]
RUN_5_CRITERIA_10_11 = [
    "If the data file exists but cannot be read as a Habits database, Habits shows a clear "
    "plain-English message that the file could not be read, instead of an empty list or a "
    "stack trace. The file is left byte-for-byte unchanged and is not replaced by a new empty "
    "file.",
    "Requests that change data are refused with status 403, and save nothing, when their Host "
    "header is not 127.0.0.1:<PORT> or localhost:<PORT>, or when an Origin header is present "
    "and is not one of those two addresses.",
]
RUN_5_CLAIMS_10_11 = [
    "AC10 – Unreadable data file gives a plain-English message (503), no list, no form, no "
    "stack trace, with file bytes and folder listing unchanged: "
    "test_create_app_alone_leaves_unreadable_file_and_folder_unchanged, "
    "test_unreadable_file_shows_message_and_changes_nothing, "
    "test_habits_table_without_recorded_migration_shows_message_not_a_crash, and the garbage, "
    "foreign-SQLite, WAL, newer-version and missing-habits-table cases in test_db.py. Startup "
    "path only; see summary for the untested runtime handler.",
    "AC11 – Data-changing requests with a foreign Host or foreign Origin (including 'null') "
    "get 403 and save nothing, also in unreadable-file mode: "
    "test_post_with_foreign_host_gets_403_and_saves_nothing, "
    "test_post_with_foreign_origin_gets_403_and_saves_nothing, "
    "test_foreign_host_and_origin_get_403_and_save_nothing, "
    "test_unreadable_file_foreign_host_and_origin_still_403; allowed localhost Host and "
    "allowed Origin are also tested.",
]
RUN_5_MISSING_10_11 = [
    "AC10: An unreadable data file gives a clear plain-English message, with the file "
    "byte-for-byte unchanged and not replaced. Implemented (read-only pre-check, DATA_ERROR "
    "hook returning 503, DatabaseError handler); the tests comparing bytes and the folder "
    "listing after create_app, GET / and POST /habits are not visible.",
    "AC11: Data-changing requests with a foreign Host or a foreign Origin get 403 and save "
    "nothing. The guard is implemented and registered first; the foreign Host and Origin "
    "tests, including the unreadable-file state, are not visible.",
]


def _statuses(criteria: list[str], covered: list[str], missing: list[str]) -> list[str]:
    return [e["status"] for e in trace_criteria(criteria, covered, missing)]


class NumberedClaimTests(unittest.TestCase):
    """A claim that opens with "ACn" is about criterion n, however it is worded."""

    def test_run_6_every_numbered_claim_counts(self) -> None:
        # Live: the tester covered all 12 and gate-test said "4 AC unassessed".
        self.assertEqual(_statuses(RUN_6_CRITERIA, RUN_6_CLAIMS, []), ["covered"] * 12)

    def test_run_5_dash_and_colon_forms(self) -> None:
        pad = [f"filler criterion number {n} about nothing" for n in range(1, 10)]
        criteria = pad + RUN_5_CRITERIA_10_11
        self.assertEqual(_statuses(criteria, RUN_5_CLAIMS_10_11, [])[9:], ["covered"] * 2)
        self.assertEqual(_statuses(criteria, [], RUN_5_MISSING_10_11)[9:],
                         ["flagged_missing"] * 2)
        self.assertEqual(_statuses(criteria, RUN_5_CLAIMS_10_11, [])[:9], ["unassessed"] * 9)

    def test_a_criterion_the_tester_never_mentioned_stays_unassessed(self) -> None:
        claims = [c for c in RUN_6_CLAIMS if not c.startswith(("AC7 ", "AC9 "))]
        statuses = _statuses(RUN_6_CRITERIA, claims, [])
        self.assertEqual([i + 1 for i, s in enumerate(statuses) if s == "unassessed"], [7, 9])

    def test_a_numbered_claim_speaks_only_for_its_own_criterion(self) -> None:
        # Shares most words with criterion 2, but the tester labelled it AC1.
        criteria = ["search returns matching items", "search returns matching items quickly"]
        self.assertEqual(_statuses(criteria, ["AC1 search returns matching items"], []),
                         ["covered", "unassessed"])

    def test_a_number_beats_a_look_alike_in_the_other_list(self) -> None:
        criteria = ["search returns matching items", "results paginate"]
        statuses = _statuses(criteria, ["search returns the matching items, results paginate"],
                             ["AC1: search returns matching items - no test visible"])
        self.assertEqual(statuses[0], "flagged_missing")

    def test_other_ways_of_writing_the_number(self) -> None:
        criteria = ["alpha beta gamma", "delta epsilon zeta", "eta theta iota", "kappa lambda"]
        claims = ["AC-1: unrelated words", "ac 2 (structural) unrelated", "3. unrelated words",
                  "AC#4 unrelated"]
        self.assertEqual(_statuses(criteria, claims, []), ["covered"] * 4)

    def test_several_numbers_in_one_claim(self) -> None:
        criteria = ["alpha beta gamma", "delta epsilon zeta", "eta theta iota"]
        self.assertEqual(_statuses(criteria, ["AC1, AC3: one test for both"], []),
                         ["covered", "unassessed", "covered"])

    def test_a_number_outside_the_story_falls_back_to_the_text(self) -> None:
        criteria = ["search returns matching items"]
        self.assertEqual(_statuses(criteria, ["AC7 search returns the matching items"], []),
                         ["covered"])
        self.assertEqual(_statuses(criteria, ["AC7 nothing alike"], []), ["unassessed"])

    def test_a_number_that_is_part_of_the_text_is_not_a_reference(self) -> None:
        criteria = ["one two three", "50 characters after trimming is accepted"]
        self.assertEqual(_statuses(criteria, ["50 characters after trimming is accepted"], []),
                         ["unassessed", "covered"])


if __name__ == "__main__":
    unittest.main()
