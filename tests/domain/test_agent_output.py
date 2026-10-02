"""Tests for utility helpers."""

from __future__ import annotations

import unittest
from datetime import date

from factory.domain.agent_output import parse_agent_json, parse_date


class ParseAgentJsonTests(unittest.TestCase):
    def test_fenced_json(self) -> None:
        out = parse_agent_json('prose\n```json\n{"verdict": "pass"}\n```\nmore')
        self.assertEqual(out, {"verdict": "pass"})

    def test_bare_braces(self) -> None:
        out = parse_agent_json('here it is {"a": 1, "b": [2, 3]} done')
        self.assertEqual(out, {"a": 1, "b": [2, 3]})

    def test_invalid_returns_none(self) -> None:
        self.assertIsNone(parse_agent_json("no json here at all"))
        self.assertIsNone(parse_agent_json(""))
        self.assertIsNone(parse_agent_json("{not valid json}"))


class ParseDateTests(unittest.TestCase):
    """Test cases for ``parse_date``."""

    def test_parses_valid_date(self) -> None:
        """It returns a date object for valid input."""

        self.assertEqual(parse_date("2024-01-01"), date(2024, 1, 1))

    def test_returns_none_for_invalid_month(self) -> None:
        """It returns None for a date string with an invalid month."""

        self.assertIsNone(parse_date("2024-13-01"))

    def test_returns_none_for_other_invalid_dates(self) -> None:
        """It returns None for malformed calendar dates."""

        self.assertIsNone(parse_date("2024-02-30"))
