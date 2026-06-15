"""Tests for temperature conversion utilities."""

from __future__ import annotations

import unittest

from factory.temperature import celsius_to_fahrenheit


class CelsiusToFahrenheitTests(unittest.TestCase):
    """Test cases for Celsius to Fahrenheit conversion."""

    def test_converts_normal_value(self) -> None:
        """It converts a standard Celsius temperature correctly."""

        self.assertAlmostEqual(celsius_to_fahrenheit(0.0), 32.0)

    def test_accepts_absolute_zero_boundary(self) -> None:
        """It accepts the absolute-zero boundary value."""

        self.assertAlmostEqual(celsius_to_fahrenheit(-273.15), -459.67)

    def test_rejects_below_absolute_zero(self) -> None:
        """It rejects temperatures below absolute zero."""

        with self.assertRaisesRegex(ValueError, "absolute zero"):
            celsius_to_fahrenheit(-273.16)
