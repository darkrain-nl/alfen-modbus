"""Shared setpoint rules for Alfen charging stations."""

from __future__ import annotations

import math
from time import monotonic

__all__ = ["MIN_CHARGING_CURRENT", "NAN", "SetpointHold", "validate_max_current"]

NAN = 0xFFFF
"""The word a reserved or unavailable 16-bit register reads as."""

MIN_CHARGING_CURRENT = 6.0
"""The lowest current IEC 61851 lets a vehicle charge at, in amperes."""

# A float32 read back differs from the written float in its last digits.
READBACK_TOLERANCE = 0.01


def validate_max_current(amps: float) -> None:
    """Reject a setpoint the station cannot act on as asked."""
    if not math.isfinite(amps):
        raise ValueError(f"max current {amps} is not a finite number")
    # 0 A pauses charging; anything else under the floor is ambiguous.
    if amps != 0 and amps < MIN_CHARGING_CURRENT:
        raise ValueError(
            f"max current {amps} A must be 0 or at least {MIN_CHARGING_CURRENT} A"
        )


class SetpointHold:
    """Max currents to send again before the station lets them lapse."""

    def __init__(self, amps: tuple[float, ...]) -> None:
        for value in amps:
            validate_max_current(value)
        self.amps = amps
        self._checked_at = monotonic()

    def due(
        self, read: tuple[float | None, ...], remaining: tuple[int | None, ...]
    ) -> bool:
        """Whether the setpoint must be sent now to stay in force."""
        now = monotonic()
        try:
            # The next check comes about one gap later; keep a gap to spare.
            if any(
                seconds is None or seconds <= 2 * (now - self._checked_at)
                for seconds in remaining
            ):
                return True
            # A reboot drops the setpoint before its time runs out.
            return any(
                value is None or abs(value - wanted) > READBACK_TOLERANCE
                for value, wanted in zip(read, self.amps, strict=True)
            )
        finally:
            self._checked_at = now
