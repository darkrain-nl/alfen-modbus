from __future__ import annotations

import pytest
from modbus_connection import (
    IllegalDataAddressError,
    ModbusConnectionError,
    ServerDeviceFailureError,
)
from modbus_connection.encode import encode_float32
from modbus_connection.mock import MockModbusConnection, MockModbusUnit, WriteEvent

from alfen_modbus import AlfenCharger

from .conftest import poll_all


class Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        """Return the time the test has set."""
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr("alfen_modbus.model.monotonic", clock)
    return clock


@pytest.fixture
async def charger(connection: MockModbusConnection) -> AlfenCharger:
    charger = AlfenCharger(connection.for_unit)
    await charger.async_setup()
    return charger


def recorder(unit: MockModbusUnit) -> list[WriteEvent]:
    writes: list[WriteEvent] = []
    unit.on_write(writes.append)
    return writes


async def test_hold_writes_at_once(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock
) -> None:
    writes = recorder(socket_1)
    await charger.socket_1.status.async_hold_max_current(13.5)
    assert [write.address for write in writes] == [1210]
    assert charger.socket_1.status.held_max_current == 13.5


async def test_hold_rejected_before_writing(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock
) -> None:
    writes = recorder(socket_1)
    with pytest.raises(ValueError, match="max current 3.0 A"):
        await charger.socket_1.status.async_hold_max_current(3.0)
    assert writes == []
    assert charger.socket_1.status.held_max_current is None


async def test_hold_kept_when_write_fails(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock
) -> None:
    socket_1.fail_write(1210, IllegalDataAddressError())
    with pytest.raises(IllegalDataAddressError):
        await charger.socket_1.status.async_hold_max_current(16.0)
    assert charger.socket_1.status.held_max_current == 16.0


async def test_no_hold_no_write(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock
) -> None:
    writes = recorder(socket_1)
    clock.now += 600
    await poll_all(charger)
    assert writes == []


async def test_hold_not_resent_with_time_to_spare(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock
) -> None:
    await charger.socket_1.status.async_hold_max_current(13.3)
    writes = recorder(socket_1)
    clock.now += 10
    report = await poll_all(charger)
    # 42 s left after a 10 s gap; 13.3 reads back as 13.30000019.
    assert writes == []
    assert report.complete


@pytest.mark.parametrize("gap", [21.0, 30.0, 60.0])
async def test_hold_resent_before_lapse(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock, gap: float
) -> None:
    await charger.socket_1.status.async_hold_max_current(16.0)
    writes = recorder(socket_1)
    clock.now += gap
    await poll_all(charger)
    # 42 s left would not outlast two more gaps.
    assert [(write.address, write.values) for write in writes] == [
        (1210, encode_float32(16.0))
    ]


async def test_hold_resent_after_reboot(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock
) -> None:
    await charger.socket_1.status.async_hold_max_current(16.0)
    socket_1.holding[1210], socket_1.holding[1211] = encode_float32(6.0)
    writes = recorder(socket_1)
    clock.now += 10
    await poll_all(charger)
    assert len(writes) == 1


async def test_released_hold_not_resent(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock
) -> None:
    await charger.socket_1.status.async_hold_max_current(16.0)
    charger.socket_1.status.release_max_current()
    writes = recorder(socket_1)
    clock.now += 60
    await poll_all(charger)
    assert writes == []
    assert charger.socket_1.status.held_max_current is None


async def test_hold_only_for_polled(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock
) -> None:
    await charger.socket_1.status.async_hold_max_current(16.0)
    writes = recorder(socket_1)
    clock.now += 60
    assert charger.socket_2 is not None
    await charger.async_update((charger.status, charger.socket_2))
    assert writes == []


async def test_resend_failure_reported(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock
) -> None:
    await charger.socket_1.status.async_hold_max_current(16.0)
    socket_1.fail_write(1210, ServerDeviceFailureError())
    calls: list[None] = []
    charger.socket_1.status.add_update_listener(lambda: calls.append(None))
    clock.now += 60
    report = await poll_all(charger)
    assert charger.socket_1 in report.updated
    assert isinstance(report.rewrite_failed[charger.socket_1], ServerDeviceFailureError)
    assert not report.complete
    assert calls == [None]


async def test_resend_connection_lost_raises(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock
) -> None:
    await charger.socket_1.status.async_hold_max_current(16.0)
    socket_1.fail_write(1210, ModbusConnectionError("lost"))
    clock.now += 60
    with pytest.raises(ModbusConnectionError):
        await poll_all(charger)


async def test_scn_hold_resent_in_one_request(
    charger: AlfenCharger, station: MockModbusUnit, clock: Clock
) -> None:
    assert charger.scn is not None
    await charger.scn.async_hold_max_currents(20.0, 16.0, 0.0)
    assert charger.scn.held_max_currents == (20.0, 16.0, 0.0)
    writes = recorder(station)
    # Only phase 3 runs short: 10 s left after a 10 s gap.
    station.holding[1427], station.holding[1428] = 0, 10
    clock.now += 10
    await poll_all(charger)
    assert [(write.address, write.values) for write in writes] == [
        (
            1417,
            [
                *encode_float32(20.0),
                *encode_float32(16.0),
                *encode_float32(0.0),
            ],
        )
    ]


async def test_scn_hold_not_resent_with_time_to_spare(
    charger: AlfenCharger, station: MockModbusUnit, clock: Clock
) -> None:
    assert charger.scn is not None
    await charger.scn.async_hold_max_currents(20.0, 16.0, 0.0)
    writes = recorder(station)
    clock.now += 10
    await poll_all(charger)
    assert writes == []


async def test_scn_released_hold_not_resent(
    charger: AlfenCharger, station: MockModbusUnit, clock: Clock
) -> None:
    assert charger.scn is not None
    await charger.scn.async_hold_max_currents(20.0, 16.0, 0.0)
    charger.scn.release_max_currents()
    writes = recorder(station)
    clock.now += 60
    await poll_all(charger)
    assert writes == []
    assert charger.scn.held_max_currents is None
