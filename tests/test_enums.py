from __future__ import annotations

import pytest

from alfen_modbus import Mode3State

# Appendix F of the guide: (connected, charging, error) per state.
TABLE = {
    Mode3State.A: (False, False, False),
    Mode3State.B1: (True, False, False),
    Mode3State.B2: (True, False, False),
    Mode3State.C1: (True, False, False),
    Mode3State.C2: (True, True, False),
    Mode3State.D1: (True, False, False),
    Mode3State.D2: (True, True, False),
    Mode3State.E: (False, False, True),
    Mode3State.F: (False, False, True),
}


def test_table_covers_every_state() -> None:
    assert len(TABLE) == 9
    assert set(TABLE) == set(Mode3State)


@pytest.mark.parametrize(("state", "expected"), TABLE.items())
def test_mode_3_state(state: Mode3State, expected: tuple[bool, bool, bool]) -> None:
    assert (state.vehicle_connected, state.charging, state.error) == expected
