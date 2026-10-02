"""General utility helpers for the factory package."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


def parse_agent_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from agent output, or None if none parses.

    Tries a ```json fenced block first, then the outermost brace span. Shared by
    the pipeline (which wraps None as a synthetic 'blocked' result) and the CLI
    review renderer (which treats None as 'no structured output').
    """
    if not text:
        return None
    fence = _FENCE_RE.search(text)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None


def parse_date(value: str) -> date | None:
    """Parse an ISO-style date string.

    Args:
        value: A date string in ``YYYY-MM-DD`` format.

    Returns:
        A ``date`` instance for valid input, or ``None`` when parsing fails.
    """

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
