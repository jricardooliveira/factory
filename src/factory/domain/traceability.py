"""Acceptance-criteria traceability (deterministic, no LLM).

Closes the "AC silently dropped" gap: instead of trusting the tester's
self-reported `ac_coverage`, cross-check the spec's REAL acceptance criteria
against what the tester claimed. Each criterion ends up classified as:

- ``covered``         — the tester claimed a test exercises it,
- ``flagged_missing`` — the tester explicitly said it lacks coverage,
- ``unassessed``      — the tester never mentioned it at all (the silent drop).

A claim that opens with the criterion's number ("AC2 ...", "AC10 – ...", "3. ...")
is about that criterion and only that one — testers number their claims by the
criterion's position in the story and paraphrase freely. A claim with no number is
matched by token overlap, so a paraphrase still counts; an AC is only ``unassessed``
when the tester ignored it outright — a low-false-positive signal. This evidence
feeds the trust package (release sign-off, §5.1).
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


# "AC2", "AC-2", "ac 2", "AC#2" — also a run of them ("AC1, AC3:", "AC1/AC2").
_AC_REFS = re.compile(r"^\W*((?:AC[\s#-]*\d+\s*(?:(?:,|/|&|and)\s*)?)+)", re.IGNORECASE)
# A bare list number: "3. ...", "3) ...", "3: ..." (not "50 characters ...").
_BARE_REF = re.compile(r"^\s*(\d+)[.):]\s")


def _references(claim: str, count: int) -> set[int]:
    """The 0-based criteria a claim names by number at its start (empty if none).

    A number outside 1..count names nothing, so the claim falls back to its text.
    ponytail: a range ("AC1-AC3") counts as its first number only; expand it if a
    tester ever writes one.
    """
    refs = _AC_REFS.match(claim)
    numbers = re.findall(r"\d+", refs.group(1)) if refs else _BARE_REF.findall(claim)
    return {int(n) - 1 for n in numbers if 1 <= int(n) <= count}


def _index(claims: list[str], count: int) -> tuple[set[int], list[set[str]]]:
    """(criteria named by number, token sets of the claims that named none)."""
    named: set[int] = set()
    unnumbered: list[set[str]] = []
    for claim in claims:
        refs = _references(claim, count)
        named |= refs
        if not refs:
            unnumbered.append(_tokens(claim))
    return named, unnumbered


def trace_criteria(
    acceptance_criteria: list[str],
    ac_coverage: list[str],
    missing_coverage: list[str],
) -> list[dict[str, str]]:
    """Classify each acceptance criterion as covered / flagged_missing / unassessed."""
    count = len(acceptance_criteria)
    covered_refs, covered_sets = _index(ac_coverage, count)
    missing_refs, missing_sets = _index(missing_coverage, count)
    result: list[dict[str, str]] = []
    for position, ac in enumerate(acceptance_criteria):
        ac_tokens = _tokens(ac)
        # What the tester said by number beats what merely looks alike.
        if position in covered_refs:
            status = "covered"
        elif position in missing_refs:
            status = "flagged_missing"
        elif any(_fuzzy_match(ac_tokens, c) for c in covered_sets):
            status = "covered"
        elif any(_fuzzy_match(ac_tokens, c) for c in missing_sets):
            status = "flagged_missing"
        else:
            status = "unassessed"
        result.append({"criterion": ac, "status": status})
    return result


def unassessed_criteria(trace: list[dict[str, str]]) -> list[str]:
    """Criteria the tester never assessed — the silent drops to surface."""
    return [e["criterion"] for e in trace if e["status"] == "unassessed"]
