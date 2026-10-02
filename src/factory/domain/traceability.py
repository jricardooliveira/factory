"""Acceptance-criteria traceability (deterministic, no LLM).

Closes the "AC silently dropped" gap: instead of trusting the tester's
self-reported `ac_coverage`, cross-check the spec's REAL acceptance criteria
against what the tester claimed. Each criterion ends up classified as:

- ``covered``         — the tester claimed a test exercises it,
- ``flagged_missing`` — the tester explicitly said it lacks coverage,
- ``unassessed``      — the tester never mentioned it at all (the silent drop).

Matching is fuzzy (token overlap) so a paraphrased claim still counts; an AC is
only ``unassessed`` when the tester ignored it outright — a low-false-positive
signal. This evidence feeds the trust package (release sign-off, §5.1).
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[a-z0-9]+")
_MATCH_THRESHOLD = 0.5


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _fuzzy_match(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    overlap = len(a & b)
    return overlap / min(len(a), len(b)) >= _MATCH_THRESHOLD


def _matched_by_any(ac: set[str], claim_sets: list[set[str]]) -> bool:
    return any(_fuzzy_match(ac, c) for c in claim_sets)


def trace_criteria(
    acceptance_criteria: list[str],
    ac_coverage: list[str],
    missing_coverage: list[str],
) -> list[dict[str, str]]:
    """Classify each acceptance criterion as covered / flagged_missing / unassessed."""
    covered_sets = [_tokens(c) for c in ac_coverage]
    missing_sets = [_tokens(c) for c in missing_coverage]
    result: list[dict[str, str]] = []
    for ac in acceptance_criteria:
        ac_tokens = _tokens(ac)
        if _matched_by_any(ac_tokens, covered_sets):
            status = "covered"
        elif _matched_by_any(ac_tokens, missing_sets):
            status = "flagged_missing"
        else:
            status = "unassessed"
        result.append({"criterion": ac, "status": status})
    return result


def unassessed_criteria(trace: list[dict[str, str]]) -> list[str]:
    """Criteria the tester never assessed — the silent drops to surface."""
    return [e["criterion"] for e in trace if e["status"] == "unassessed"]
