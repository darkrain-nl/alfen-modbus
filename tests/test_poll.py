from __future__ import annotations

import pytest
from modbus_connection import (
    IllegalDataAddressError,
    ModbusConnectionError,
    ModbusError,
    ModbusTimeoutError,
    ServerDeviceFailureError,
)
from modbus_connection.encode import encode_float32
from modbus_connection.mock import MockModbusConnection, MockModbusUnit

from alfen_modbus import AlfenCharger, NothingAnsweredError


async def test_components_before_setup(connection: MockModbusConnection) -> None:
    charger = AlfenCharger(connection.for_unit)
    with pytest.raises(RuntimeError, match="async_setup"):
        await charger.async_update(charger.components)


async def test_sockets_before_setup(connection: MockModbusConnection) -> None:
    charger = AlfenCharger(connection.for_unit)
    with pytest.raises(RuntimeError, match="async_setup"):
        await charger.async_update(charger.sockets)


async def test_setup_runs_once(
    connection: MockModbusConnection, station: MockModbusUnit
) -> None:
    charger = AlfenCharger(connection.for_unit)
    await charger.async_setup()
    reads = len(station.read_events)
    await charger.async_setup()
    assert len(station.read_events) == reads


async def test_setup_retried_after_failure(
    connection: MockModbusConnection, station: MockModbusUnit
) -> None:
    charger = AlfenCharger(connection.for_unit)
    station.fail_read(100, ModbusTimeoutError())
    with pytest.raises(ModbusTimeoutError):
        await charger.async_setup()
    station.fail_read(100, None)
    await charger.async_setup()
    assert charger.identity.name == "ALF_1000"


async def test_poll_only_given(
    connection: MockModbusConnection,
    station: MockModbusUnit,
    socket_1: MockModbusUnit,
) -> None:
    charger = AlfenCharger(connection.for_unit)
    await charger.async_setup()
    assert charger.socket_2 is not None
    station.read_events.clear()
    report = await charger.async_update((charger.socket_1, charger.socket_2))
    assert report.updated == {charger.socket_1, charger.socket_2}
    assert station.read_events == []
    assert socket_1.read_events


async def test_refusal_lands_in_failed(
    connection: MockModbusConnection, socket_1: MockModbusUnit
) -> None:
    charger = AlfenCharger(connection.for_unit)
    await charger.async_setup()
    socket_1.fail_read(1200, IllegalDataAddressError())
    report = await charger.async_update((charger.status, charger.socket_1))
    assert report.updated == {charger.status}
    assert report.failed.keys() == {charger.socket_1}
    assert isinstance(report.failed[charger.socket_1], IllegalDataAddressError)
    assert not report.complete


async def test_nothing_answered_raises(
    connection: MockModbusConnection,
    station: MockModbusUnit,
    socket_1: MockModbusUnit,
) -> None:
    charger = AlfenCharger(connection.for_unit)
    await charger.async_setup()
    station.fail_read(1100, ServerDeviceFailureError())
    socket_1.fail_read(300, ModbusTimeoutError())
    calls: list[None] = []
    charger.status.add_update_listener(lambda: calls.append(None))
    with pytest.raises(NothingAnsweredError, match="all 2 polled") as err:
        await charger.async_update((charger.status, charger.socket_1))
    assert err.value.failed.keys() == {charger.status, charger.socket_1}
    assert isinstance(err.value, ModbusError)
    assert calls == []


async def test_nothing_polled_returns_empty(
    connection: MockModbusConnection,
) -> None:
    charger = AlfenCharger(connection.for_unit)
    report = await charger.async_update(())
    assert report.complete
    assert not report.updated


async def test_first_timeout_raises(
    connection: MockModbusConnection, station: MockModbusUnit
) -> None:
    charger = AlfenCharger(connection.for_unit)
    await charger.async_setup()
    station.fail_read(1100, ModbusTimeoutError())
    with pytest.raises(ModbusTimeoutError):
        await charger.async_update((charger.status, charger.socket_1))


async def test_later_timeout_lands_in_failed(
    connection: MockModbusConnection, socket_1: MockModbusUnit
) -> None:
    charger = AlfenCharger(connection.for_unit)
    await charger.async_setup()
    socket_1.fail_read(300, ModbusTimeoutError())
    report = await charger.async_update((charger.status, charger.socket_1))
    assert report.failed.keys() == {charger.socket_1}
    assert isinstance(report.failed[charger.socket_1], ModbusTimeoutError)


async def test_connection_error_raises(
    connection: MockModbusConnection, socket_1: MockModbusUnit
) -> None:
    charger = AlfenCharger(connection.for_unit)
    await charger.async_setup()
    socket_1.fail_read(300, ModbusConnectionError())
    with pytest.raises(ModbusConnectionError):
        await charger.async_update((charger.status, charger.socket_1))


async def test_listeners_fire_after_every_read(
    connection: MockModbusConnection, station: MockModbusUnit
) -> None:
    charger = AlfenCharger(connection.for_unit)
    await charger.async_setup()
    station.holding[1102] = encode_float32(40.0)
    seen: list[float | None] = []
    charger.socket_1.meter.add_update_listener(
        lambda: seen.append(charger.status.temperature)
    )
    await charger.async_update((charger.socket_1, charger.status))
    assert seen == [40.0]
