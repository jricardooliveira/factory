"""Deterministic ambiguity detection for Checkpoint 1 (pure, no I/O).

Split out of gates.py: which words oblige an implementation to invent a number,
whether a criterion or request supplies that number, and which terms a
project's committed context has already settled. gate_after_spec consumes it.
"""

from __future__ import annotations

import re

# ── Deterministic ambiguity detection (Checkpoint 1) ──────────────
# A live test of the SupportFlow challenge's Story 10 ran the SAME vague request
# twice: once the spec-agent asked four precise product questions, once it
# returned `verdict: pass` and went to build, deferring the definition to a coder
# task. Story 10 exists precisely to check the pipeline does not silently invent a
# business rule — and the factory was satisfying that criterion by luck. For a
# system whose premise is "policy is deterministic Python, never delegated to an
# LLM", this is the wrong thing to leave to the model.
#
# Terms that oblige the implementation to invent a NUMBER it was never given.
# Deliberately NARROW: quality adjectives ("appropriate", "reasonable", "proper")
# are EXCLUDED. The challenge uses "appropriate" in eight of fifteen stories, so
# flagging those would park nearly every story and train the operator to click
# through — the crying-wolf failure this check exists to avoid. Judging test
# adequacy is the tester's job via REVIEW.md, not gate-1's.
THRESHOLD_TERMS: frozenset[str] = frozenset({
    # temporal cutoffs
    "overdue", "stale", "expired", "recent", "recently", "soon", "late", "delayed",
    "timely", "outdated", "aging", "aged", "lapsed",
    # magnitude cutoffs
    "large", "small", "long", "short", "slow", "fast", "quick", "heavy", "oversized",
    "high-volume", "bulk", "busy",
    # activity / popularity cutoffs
    "active", "inactive", "idle", "popular", "trending", "top",
    # cadence cutoffs
    "frequent", "frequently", "periodic", "periodically", "regularly", "often",
    # proximity cutoffs
    "nearby", "close", "adjacent",
})

# Evidence that a criterion DOES carry its threshold.
_NUMBER_WORDS = (
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "fifteen", "twenty", "thirty", "sixty", "hundred", "thousand",
)
# Matched as WHOLE WORDS. A substring test here was a real bug: "nu(mb)er" hit
# the megabyte unit, and "ms" matches almost any -ms plural ("items", "terms",
# "problems"), which silently disabled the whole check on ordinary criteria.
_UNIT_WORDS = frozenset({
    "second", "seconds", "minute", "minutes", "hour", "hours",
    "day", "days", "week", "weeks", "month", "months", "year", "years",
    "ms", "sec", "secs", "kb", "mb", "gb", "percent",
})
_WORD_RE = re.compile(r"[a-z0-9%\-]+")


def is_bound(criterion: str) -> bool:
    """Whether a criterion supplies the number its qualifier needs."""
    lowered = criterion.lower()
    if any(ch.isdigit() for ch in lowered) or "%" in lowered:
        return True
    words = set(_WORD_RE.findall(lowered))
    return bool(words & set(_NUMBER_WORDS)) or bool(words & _UNIT_WORDS)


def defined_threshold_terms(project_context: str) -> frozenset[str]:
    """Threshold terms already SETTLED in the project's committed context.

    Once a prior ADR or PROJECT_RULES.md defines "overdue", a later story that
    counts overdue tickets must not park again — the factory does not
    re-litigate settled decisions (EFFECTIVENESS §7). Fed from the same
    PROJECT_RULES + prior-ADR block the agents already receive, so the memory
    that informs the agents is the memory that relaxes the gate.
    """
    if not project_context:
        return frozenset()
    words = set(_WORD_RE.findall(project_context.lower()))
    return frozenset(t for t in THRESHOLD_TERMS if t in words)


def unasked_request_terms(
    request: str, defined_terms: frozenset[str] = frozenset()
) -> frozenset[str]:
    """Threshold terms the OPERATOR used without supplying a number.

    These need sign-off however the agents resolve them. Keying only off the
    acceptance criteria let an INVENTED number through: a criterion reading
    "created_at is older than 48 hours" looks bound, but the request never said
    48 hours, and an earlier run had picked 24. Writing the number down makes the
    invention visible; it does not make it authorised.
    """
    if not request:
        return frozenset()
    if is_bound(request):
        # The operator supplied a number, so nothing was invented.
        return frozenset()
    words = set(_WORD_RE.findall(request.lower()))
    return frozenset(t for t in THRESHOLD_TERMS if t in words and t not in defined_terms)


def unbound_criteria(
    acceptance_criteria: list[str],
    *,
    request: str = "",
    defined_terms: frozenset[str] = frozenset(),
) -> list[tuple[str, str]]:
    """Criteria carrying a threshold nobody authorised.

    Two ways in:
      - the criterion uses a threshold term and supplies NO number, or
      - the REQUEST used that term without a number, so any resolution of it —
        including a number the agent chose — is unapproved.

    Returns ``[(criterion, term)]``, at most one finding per criterion so a
    sentence with two qualifiers does not double-report.
    """
    needs_approval = unasked_request_terms(request, defined_terms)
    findings: list[tuple[str, str]] = []
    for criterion in acceptance_criteria:
        words = set(_WORD_RE.findall(criterion.lower()))
        candidates = [
            t for t in sorted(THRESHOLD_TERMS)
            if t in words and t not in defined_terms
        ]
        if not candidates:
            continue
        # An unauthorised term from the request always needs approval; otherwise
        # only an unquantified criterion does.
        hit = next((t for t in candidates if t in needs_approval), None)
        if hit is None and not is_bound(criterion):
            hit = candidates[0]
        if hit:
            findings.append((criterion, hit))
    return findings
