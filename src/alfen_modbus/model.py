"""Shared setpoint rules for Alfen charging stations."""

from __future__ import annotations

import math
from enum import Enum, auto
from time import monotonic

__all__ = [
    "MIN_CHARGING_CURRENT",
    "NAN",
    "HoldCheck",
    "SetpointHold",
    "validate_max_current",
]

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


class HoldCheck(Enum):
    """What a poll found about a held setpoint."""

    KEEP = auto()
    RESEND = auto()
    TAKEN_OVER = auto()


class SetpointHold:
    """Max currents to send again before the station lets them lapse."""

    def __init__(self, amps: tuple[float, ...]) -> None:
        for value in amps:
            validate_max_current(value)
        self.amps = amps
        self.sent = False
        self._checked_at = monotonic()

    def check(
        self, read: tuple[float | None, ...], remaining: tuple[int | None, ...]
    ) -> HoldCheck:
        """Decide whether the setpoint needs sending to stay in force."""
        now = monotonic()
        try:
            # The station may still hold our previous value, not this one.
            if not self.sent:
                return HoldCheck.RESEND
            # Time left on a value we did not send: another client wrote it.
            if any(
                seconds
                for value, wanted, seconds in zip(
                    read, self.amps, remaining, strict=True
                )
                if value is None or abs(value - wanted) > READBACK_TOLERANCE
            ):
                return HoldCheck.TAKEN_OVER
            # A lapse or reboot leaves 0 s; the next poll is a gap away.
            if any(
                seconds is None or seconds <= 2 * (now - self._checked_at)
                for seconds in remaining
            ):
                return HoldCheck.RESEND
            return HoldCheck.KEEP
        finally:
            self._checked_at = now
