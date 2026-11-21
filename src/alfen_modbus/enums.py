"""Enumerations for Alfen charging stations."""

from __future__ import annotations

from enum import IntEnum, IntFlag, StrEnum


class MeterState(IntFlag):
    """State of the energy meter behind a socket."""

    INITIALISED = 0x01
    UPDATED = 0x02
    WARNING = 0x04
    ERROR = 0x08


class MeterType(IntEnum):
    """How the station talks to the energy meter behind a socket."""

    RTU = 0
    TCP_IP = 1
    UDP = 2
    P1 = 3
    OTHER = 4


class Phase(IntEnum):
    """One phase of the Smart Charging Network's supply."""

    L1 = 1
    L2 = 2
    L3 = 3


class ChargingPhases(IntEnum):
    """How many phases a socket charges on."""

    ONE = 1
    THREE = 3


class Mode3State(StrEnum):
    """IEC 61851 Mode 3 state of a socket."""

    A = "A"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"
    D1 = "D1"
    D2 = "D2"
    E = "E"
    F = "F"

    @property
    def vehicle_connected(self) -> bool:
        """Whether a vehicle is plugged in."""
        return self in _CONNECTED

    @property
    def charging(self) -> bool:
        """Whether the vehicle is drawing current."""
        return self in _CHARGING

    @property
    def error(self) -> bool:
        """Whether the socket is in an error state."""
        return self in _ERRORS


_CONNECTED = frozenset(
    {
        Mode3State.B1,
        Mode3State.B2,
        Mode3State.C1,
        Mode3State.C2,
        Mode3State.D1,
        Mode3State.D2,
    }
)
_CHARGING = frozenset({Mode3State.C2, Mode3State.D2})
_ERRORS = frozenset({Mode3State.E, Mode3State.F})
