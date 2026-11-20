"""Shared limits for Alfen charging stations."""

from __future__ import annotations

import math

__all__ = ["MIN_CHARGING_CURRENT", "NAN", "validate_max_current"]

NAN = 0xFFFF
"""The word a reserved or unavailable 16-bit register reads as."""

MIN_CHARGING_CURRENT = 6.0
"""The lowest current IEC 61851 lets a vehicle charge at, in amperes."""


def validate_max_current(amps: float) -> None:
    """Reject a setpoint the station cannot act on as asked."""
    if not math.isfinite(amps):
        raise ValueError(f"max current {amps} is not a finite number")
    # 0 A pauses charging; anything else under the floor is ambiguous.
    if amps != 0 and amps < MIN_CHARGING_CURRENT:
        raise ValueError(
            f"max current {amps} A must be 0 or at least {MIN_CHARGING_CURRENT} A"
        )
