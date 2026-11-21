from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from modbus_connection import IllegalDataAddressError
from modbus_connection.mock import MockModbusConnection, MockModbusUnit

from alfen_modbus import (
    STATION_UNIT,
    AlfenCharger,
    ChargingPhases,
    MeterState,
    MeterType,
    Mode3State,
    StationIdentity,
)

from .conftest import NAN_32, poll_all


async def test_identity(connection: MockModbusConnection) -> None:
    charger = AlfenCharger(connection.for_unit)
    await poll_all(charger)
    identity = charger.identity
    assert identity.name == "ALF_1000"
    assert identity.manufacturer == "Alfen B.V."
    assert identity.modbus_table_version == 1
    assert identity.firmware_version == "6.4.0-4210"
    assert identity.platform_type == "NG920"
    assert identity.serial_number == "ACE0012345"


async def test_identity_detect(connection: MockModbusConnection) -> None:
    identity = await StationIdentity.async_detect(connection.for_unit(STATION_UNIT))
    assert identity.serial_number == "ACE0012345"
    assert identity.name == "ALF_1000"


async def test_clock(connection: MockModbusConnection) -> None:
    charger = AlfenCharger(connection.for_unit)
    await poll_all(charger)
    assert charger.clock.uptime == 100.0
    assert charger.clock.local_time == datetime(
        2026, 11, 20, 14, 30, 5, tzinfo=timezone(timedelta(hours=1))
    )


async def test_clock_unset(
    connection: MockModbusConnection, station: MockModbusUnit
) -> None:
    station.holding[178] = 0xFFFF
    charger = AlfenCharger(connection.for_unit)
    await poll_all(charger)
    assert charger.clock.utc_offset is None
    assert charger.clock.local_time is None


async def test_status(connection: MockModbusConnection) -> None:
    charger = AlfenCharger(connection.for_unit)
    await poll_all(charger)
    assert charger.status.active_max_current == 16.0
    assert charger.status.temperature == 38.5
    assert charger.status.backoffice_connected is True
    assert charger.status.socket_count == 2


async def test_poll_reports_every_component(
    connection: MockModbusConnection,
) -> None:
    charger = AlfenCharger(connection.for_unit)
    report = await poll_all(charger)
    assert charger.scn is not None
    assert charger.socket_2 is not None
    assert report.updated == {
        charger.clock,
        charger.status,
        charger.scn,
        charger.socket_1,
        charger.socket_2,
    }
    assert report.complete
    assert charger.sockets == (charger.socket_1, charger.socket_2)


async def test_single_socket(
    connection: MockModbusConnection, station: MockModbusUnit
) -> None:
    station.holding[1105] = 1
    charger = AlfenCharger(connection.for_unit)
    report = await poll_all(charger)
    assert charger.socket_2 is None
    assert charger.sockets == (charger.socket_1,)
    assert len(report.updated) == 4
    assert charger.socket_1 in report.updated


async def test_scn_refused(
    connection: MockModbusConnection, station: MockModbusUnit
) -> None:
    station.fail_read(1400, IllegalDataAddressError())
    charger = AlfenCharger(connection.for_unit)
    report = await poll_all(charger)
    assert charger.scn is None
    assert len(report.updated) == 4
    assert charger.socket_2 in report.updated


async def test_scn(connection: MockModbusConnection) -> None:
    charger = AlfenCharger(connection.for_unit)
    await poll_all(charger)
    scn = charger.scn
    assert scn is not None
    assert scn.name == "ALF_SCN"
    assert scn.socket_count == 2
    assert (scn.consumption_l1, scn.consumption_l2, scn.consumption_l3) == (
        10.5,
        11.0,
        9.5,
    )
    assert scn.actual_max_current_l1 == 25.0
    assert scn.max_current_l3 == 20.0
    assert (scn.valid_time_l1, scn.valid_time_l2, scn.valid_time_l3) == (45, 46, 47)
    assert scn.safe_current == 6.0
    assert scn.max_current_enabled is True


async def test_socket_meter(connection: MockModbusConnection) -> None:
    charger = AlfenCharger(connection.for_unit)
    await poll_all(charger)
    meter = charger.socket_1.meter
    assert meter.state == MeterState.INITIALISED | MeterState.UPDATED
    assert meter.reading_age == 1.5
    assert meter.meter_type is MeterType.RTU
    assert meter.voltage_l1_n == pytest.approx(230.1)
    assert meter.voltage_l2_l3 == 401.0
    assert meter.current_l1 == 16.0
    assert meter.frequency == pytest.approx(50.01)
    assert meter.real_power_sum == 3680.0
    assert meter.reactive_power_l1 == -12.5
    assert meter.real_energy_delivered_sum == 1234567.5
    assert meter.apparent_energy_l1 == 2000000.0
    assert meter.reactive_energy_sum == 1500.0


async def test_socket_meter_nan_sums(
    connection: MockModbusConnection, socket_1: MockModbusUnit
) -> None:
    socket_1.holding[360] = NAN_32
    charger = AlfenCharger(connection.for_unit)
    await poll_all(charger)
    meter = charger.socket_1.meter
    assert meter.current_sum is None
    assert meter.apparent_power_sum is None
    assert meter.reactive_power_sum is None
    assert meter.real_energy_consumed_sum is None


async def test_socket_status(connection: MockModbusConnection) -> None:
    charger = AlfenCharger(connection.for_unit)
    await poll_all(charger)
    assert charger.socket_2 is not None
    status = charger.socket_2.status
    assert charger.socket_2.number == 2
    assert status.operative is True
    assert status.mode_3_state is Mode3State.C2
    assert status.actual_max_current == 16.0
    assert status.max_current_valid_time == 42
    assert status.max_current == 16.0
    assert status.safe_current == 6.0
    assert status.setpoint_accounted_for is True
    assert status.charging_phases is ChargingPhases.THREE


@pytest.mark.parametrize("code", [[0xFFFF] * 5, [0x5A00, 0, 0, 0, 0]])
async def test_mode_3_state_unknown(
    connection: MockModbusConnection, socket_1: MockModbusUnit, code: list[int]
) -> None:
    socket_1.holding[1201] = code
    charger = AlfenCharger(connection.for_unit)
    await poll_all(charger)
    assert charger.socket_1.status.mode_3_state is None


async def test_mode_3_state_before_poll(connection: MockModbusConnection) -> None:
    assert AlfenCharger(connection.for_unit).socket_1.status.mode_3_state is None


async def test_unit_factory(connection: MockModbusConnection) -> None:
    requested: list[int] = []

    def for_unit(unit_id: int) -> MockModbusUnit:
        requested.append(unit_id)
        return connection.for_unit(unit_id)

    charger = AlfenCharger(for_unit)
    assert requested == [200, 1]
    await poll_all(charger)
    assert requested == [200, 1, 2]
