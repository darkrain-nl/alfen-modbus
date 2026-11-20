from __future__ import annotations

import math

import pytest
from modbus_connection.mock import MockModbusConnection, MockModbusUnit

from alfen_modbus import AlfenCharger, ChargingPhases


@pytest.mark.parametrize("amps", [0.0, 6.0, 13.5, 32.0])
async def test_socket_max_current(
    connection: MockModbusConnection, socket_1: MockModbusUnit, amps: float
) -> None:
    charger = AlfenCharger(connection.for_unit)
    await charger.socket_1.status.async_write_max_current(amps)
    await charger.async_update()
    assert charger.socket_1.status.max_current == amps


@pytest.mark.parametrize("amps", [-1.0, 0.1, 5.9, math.nan, math.inf])
async def test_socket_max_current_rejected(
    connection: MockModbusConnection, socket_1: MockModbusUnit, amps: float
) -> None:
    writes: list[object] = []
    socket_1.on_write(writes.append)
    with pytest.raises(ValueError, match="max current"):
        await AlfenCharger(connection.for_unit).socket_1.status.async_write_max_current(
            amps
        )
    assert writes == []


async def test_socket_max_current_one_request(
    connection: MockModbusConnection, socket_1: MockModbusUnit
) -> None:
    writes: list[object] = []
    socket_1.on_write(writes.append)
    await AlfenCharger(connection.for_unit).socket_1.status.async_write_max_current(
        16.0
    )
    assert len(writes) == 1
    assert socket_1.holding[1210] == 0x4180
    assert socket_1.holding[1211] == 0x0000


async def test_charging_phases(
    connection: MockModbusConnection, socket_1: MockModbusUnit
) -> None:
    charger = AlfenCharger(connection.for_unit)
    await charger.socket_1.status.async_write_charging_phases(ChargingPhases.ONE)
    assert socket_1.holding[1215] == 1


async def test_scn_max_currents(
    connection: MockModbusConnection, station: MockModbusUnit
) -> None:
    writes: list[object] = []
    station.on_write(writes.append)
    charger = AlfenCharger(connection.for_unit)
    await charger.async_update()
    assert charger.scn is not None
    await charger.scn.async_write_max_currents(10.0, 0.0, 32.0)
    await charger.async_update()
    assert len(writes) == 1
    assert (
        charger.scn.max_current_l1,
        charger.scn.max_current_l2,
        charger.scn.max_current_l3,
    ) == (10.0, 0.0, 32.0)


async def test_scn_max_currents_rejected(
    connection: MockModbusConnection, station: MockModbusUnit
) -> None:
    charger = AlfenCharger(connection.for_unit)
    await charger.async_update()
    assert charger.scn is not None
    writes: list[object] = []
    station.on_write(writes.append)
    with pytest.raises(ValueError, match="max current 3.0 A"):
        await charger.scn.async_write_max_currents(10.0, 10.0, 3.0)
    assert writes == []
