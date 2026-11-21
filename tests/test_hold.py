from __future__ import annotations

import logging

import pytest
from modbus_connection import (
    IllegalDataAddressError,
    ModbusConnectionError,
    ServerDeviceFailureError,
)
from modbus_connection.encode import encode_float32
from modbus_connection.mock import MockModbusConnection, MockModbusUnit, WriteEvent

from alfen_modbus import AlfenCharger, Phase

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
    charger: AlfenCharger,
    socket_1: MockModbusUnit,
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
    gap: float,
) -> None:
    caplog.set_level(logging.DEBUG, "alfen_modbus")
    await charger.socket_1.status.async_hold_max_current(16.0)
    writes = recorder(socket_1)
    clock.now += gap
    await poll_all(charger)
    # 42 s left would not outlast two more gaps.
    assert [(write.address, write.values) for write in writes] == [
        (1210, encode_float32(16.0))
    ]
    assert "ACE0012345: resent socket 1's held max current" in caplog.text


@pytest.mark.parametrize("value", [encode_float32(6.0), [0x7FC0, 0x0000]])
async def test_hold_resent_after_reboot(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock, value: list[int]
) -> None:
    await charger.socket_1.status.async_hold_max_current(16.0)
    socket_1.holding[1210], socket_1.holding[1211] = value
    socket_1.holding[1208], socket_1.holding[1209] = 0, 0
    writes = recorder(socket_1)
    clock.now += 10
    report = await poll_all(charger)
    assert len(writes) == 1
    assert report.setpoint_taken_over == set()
    assert charger.socket_1.status.held_max_current == 16.0


async def test_hold_dropped_when_taken_over(
    charger: AlfenCharger,
    socket_1: MockModbusUnit,
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, "alfen_modbus")
    await charger.socket_1.status.async_hold_max_current(16.0)
    # Another client wrote 6 A, and its time is still running.
    socket_1.holding[1210], socket_1.holding[1211] = encode_float32(6.0)
    writes = recorder(socket_1)
    clock.now += 60
    report = await poll_all(charger)
    assert writes == []
    assert report.setpoint_taken_over == {charger.socket_1}
    assert charger.socket_1.status.held_max_current is None
    assert charger.socket_1.status.max_current == 6.0
    assert "ACE0012345: socket 1 reads a max current we did not" in caplog.text
    clock.now += 60
    assert (await poll_all(charger)).setpoint_taken_over == set()
    assert writes == []


async def test_failed_first_write_not_taken_over(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock
) -> None:
    await charger.socket_1.status.async_hold_max_current(10.0)
    socket_1.fail_write(1210, ServerDeviceFailureError())
    with pytest.raises(ServerDeviceFailureError):
        await charger.socket_1.status.async_hold_max_current(16.0)
    socket_1.fail_write(1210, None)
    writes = recorder(socket_1)
    clock.now += 10
    report = await poll_all(charger)
    # The station still holds our 10 A with time left; resend the 16 A.
    assert [(write.address, write.values) for write in writes] == [
        (1210, encode_float32(16.0))
    ]
    assert report.setpoint_taken_over == set()


async def test_hold_same_value_writes_nothing(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock
) -> None:
    await charger.socket_1.status.async_hold_max_current(16.0)
    writes = recorder(socket_1)
    await charger.socket_1.status.async_hold_max_current(16.0)
    assert writes == []
    await charger.socket_1.status.async_hold_max_current(10.0)
    assert len(writes) == 1
    assert charger.socket_1.status.held_max_current == 10.0


async def test_hold_same_value_retried_after_failure(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock
) -> None:
    socket_1.fail_write(1210, ServerDeviceFailureError())
    with pytest.raises(ServerDeviceFailureError):
        await charger.socket_1.status.async_hold_max_current(16.0)
    socket_1.fail_write(1210, None)
    writes = recorder(socket_1)
    await charger.socket_1.status.async_hold_max_current(16.0)
    assert len(writes) == 1


async def test_write_shows_before_poll(charger: AlfenCharger, clock: Clock) -> None:
    await poll_all(charger)
    await charger.socket_1.status.async_hold_max_current(13.5)
    assert charger.socket_1.status.max_current == 13.5
    assert charger.scn is not None
    await charger.scn.async_hold_max_current(Phase.L2, 20.0)
    assert charger.scn.max_current_l2 == 20.0
    assert charger.scn.max_current_l1 == 20.0  # as polled


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
    charger: AlfenCharger,
    socket_1: MockModbusUnit,
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, "alfen_modbus")
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
    assert "resending socket 1's held max current failed" in caplog.text


async def test_resend_connection_lost_raises(
    charger: AlfenCharger, socket_1: MockModbusUnit, clock: Clock
) -> None:
    await charger.socket_1.status.async_hold_max_current(16.0)
    socket_1.fail_write(1210, ModbusConnectionError("lost"))
    clock.now += 60
    with pytest.raises(ModbusConnectionError):
        await poll_all(charger)


async def test_scn_resends_only_the_short_phase(
    charger: AlfenCharger, station: MockModbusUnit, clock: Clock
) -> None:
    assert charger.scn is not None
    await charger.scn.async_hold_max_current(Phase.L1, 20.0)
    await charger.scn.async_hold_max_current(Phase.L3, 0.0)
    assert charger.scn.held_max_current(Phase.L3) == 0.0
    assert charger.scn.held_max_current(Phase.L2) is None
    writes = recorder(station)
    # Only phase 3 runs short: 10 s left after a 10 s gap.
    station.holding[1427], station.holding[1428] = 0, 10
    clock.now += 10
    await poll_all(charger)
    assert [(write.address, write.values) for write in writes] == [
        (1421, encode_float32(0.0))
    ]


async def test_scn_hold_not_resent_with_time_to_spare(
    charger: AlfenCharger, station: MockModbusUnit, clock: Clock
) -> None:
    assert charger.scn is not None
    for phase in Phase:
        await charger.scn.async_hold_max_current(phase, 16.0)
    writes = recorder(station)
    clock.now += 10
    await poll_all(charger)
    assert writes == []


async def test_scn_released_phase_not_resent(
    charger: AlfenCharger, station: MockModbusUnit, clock: Clock
) -> None:
    assert charger.scn is not None
    await charger.scn.async_hold_max_current(Phase.L1, 20.0)
    await charger.scn.async_hold_max_current(Phase.L2, 16.0)
    charger.scn.release_max_current(Phase.L1)
    charger.scn.release_max_current(Phase.L3)
    writes = recorder(station)
    clock.now += 60
    await poll_all(charger)
    assert [write.address for write in writes] == [1419]
    assert charger.scn.held_max_current(Phase.L1) is None
    assert charger.scn.held_max_current(Phase.L2) == 16.0


async def test_scn_hold_same_value_writes_nothing(
    charger: AlfenCharger, station: MockModbusUnit, clock: Clock
) -> None:
    assert charger.scn is not None
    await charger.scn.async_hold_max_current(Phase.L1, 20.0)
    writes = recorder(station)
    await charger.scn.async_hold_max_current(Phase.L1, 20.0)
    assert writes == []
    await charger.scn.async_hold_max_current(Phase.L2, 20.0)
    assert len(writes) == 1


async def test_scn_phase_taken_over_outranks_resend(
    charger: AlfenCharger,
    station: MockModbusUnit,
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, "alfen_modbus")
    assert charger.scn is not None
    await charger.scn.async_hold_max_current(Phase.L1, 20.0)
    await charger.scn.async_hold_max_current(Phase.L3, 16.0)
    station.holding[1421], station.holding[1422] = encode_float32(10.0)
    writes = recorder(station)
    clock.now += 60
    report = await poll_all(charger)
    # Phase 3 was taken over; phase 1 is still held and was due.
    assert [write.address for write in writes] == [1417]
    assert report.setpoint_taken_over == {charger.scn}
    assert charger.scn.held_max_current(Phase.L3) is None
    assert charger.scn.held_max_current(Phase.L1) == 20.0
    assert "the SCN reads a max current we did not write" in caplog.text
