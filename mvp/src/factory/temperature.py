"""Temperature conversion utilities."""

from __future__ import annotations


ABSOLUTE_ZERO_C = -273.15


def celsius_to_fahrenheit(celsius: float) -> float:
    """Convert Celsius to Fahrenheit.

    Args:
        celsius: Temperature in degrees Celsius.

    Returns:
        Temperature converted to degrees Fahrenheit.

    Raises:
        ValueError: If ``celsius`` is below absolute zero.
    """

    if celsius < ABSOLUTE_ZERO_C:
        raise ValueError(
            "celsius must be at or above -273.15°C (absolute zero)"
        )

    return (celsius * 9.0 / 5.0) + 32.0
