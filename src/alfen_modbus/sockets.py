"""Per-socket registers, served on Modbus unit 1 or 2."""

from __future__ import annotations

from modbus_connection import ModbusUnit
from modbus_connection.encode import encode_float32
from modbus_connection.model import (
    Component,
    ComponentGroup,
    boolean,
    enum,
    flags,
    float32,
    float64,
    string,
    uint32,
    uint64,
)

from .enums import ChargingPhases, MeterState, MeterType, Mode3State
from .model import NAN, validate_max_current


class SocketMeter(Component):
    """Measurements of the energy meter behind one socket."""

    state = flags(300, MeterState, nan=NAN)
    reading_age = uint64(301, scale=0.001, unit="s")
    meter_type = enum(305, MeterType, nan=NAN)
    voltage_l1_n = float32(306, unit="V")
    voltage_l2_n = float32(308, unit="V")
    voltage_l3_n = float32(310, unit="V")
    voltage_l1_l2 = float32(312, unit="V")
    voltage_l2_l3 = float32(314, unit="V")
    voltage_l3_l1 = float32(316, unit="V")
    current_n = float32(318, unit="A")
    current_l1 = float32(320, unit="A")
    current_l2 = float32(322, unit="A")
    current_l3 = float32(324, unit="A")
    current_sum = float32(326, unit="A")  # NaN on Reallin meters
    power_factor_l1 = float32(328)
    power_factor_l2 = float32(330)
    power_factor_l3 = float32(332)
    power_factor_sum = float32(334)
    frequency = float32(336, unit="Hz")
    real_power_l1 = float32(338, unit="W")
    real_power_l2 = float32(340, unit="W")
    real_power_l3 = float32(342, unit="W")
    real_power_sum = float32(344, unit="W")
    apparent_power_l1 = float32(346, unit="VA")
    apparent_power_l2 = float32(348, unit="VA")
    apparent_power_l3 = float32(350, unit="VA")
    apparent_power_sum = float32(352, unit="VA")  # NaN on Reallin meters
    reactive_power_l1 = float32(354, unit="var")
    reactive_power_l2 = float32(356, unit="var")
    reactive_power_l3 = float32(358, unit="var")
    reactive_power_sum = float32(360, unit="var")  # NaN on Reallin meters
    real_energy_delivered_l1 = float64(362, unit="Wh")
    real_energy_delivered_l2 = float64(366, unit="Wh")
    real_energy_delivered_l3 = float64(370, unit="Wh")
    real_energy_delivered_sum = float64(374, unit="Wh")
    real_energy_consumed_l1 = float64(378, unit="Wh")
    real_energy_consumed_l2 = float64(382, unit="Wh")
    real_energy_consumed_l3 = float64(386, unit="Wh")
    real_energy_consumed_sum = float64(390, unit="Wh")  # NaN on Reallin
    apparent_energy_l1 = float64(394, unit="VAh")
    apparent_energy_l2 = float64(398, unit="VAh")
    apparent_energy_l3 = float64(402, unit="VAh")
    apparent_energy_sum = float64(406, unit="VAh")
    reactive_energy_l1 = float64(410, unit="varh")
    reactive_energy_l2 = float64(414, unit="varh")
    reactive_energy_l3 = float64(418, unit="varh")
    reactive_energy_sum = float64(422, unit="varh")


class SocketStatus(Component):
    """Charging state and setpoints of one socket."""

    operative = boolean(1200, nan=NAN)
    mode_3_state_code = string(1201, 5)
    actual_max_current = float32(1206, unit="A")
    max_current_valid_time = uint32(1208, unit="s")
    max_current = float32(1210, unit="A")
    safe_current = float32(1212, unit="A")
    setpoint_accounted_for = boolean(1214, nan=NAN)
    charging_phases = enum(1215, ChargingPhases, nan=NAN)

    @property
    def mode_3_state(self) -> Mode3State | None:
        """The Mode 3 state; ``None`` for a code this map lacks."""
        code = self.mode_3_state_code
        if code is None or code not in Mode3State:
            return None
        return Mode3State(code)

    async def async_write_max_current(self, amps: float) -> None:
        """Set the max current, restarting its validity timer.

        The station needs Energy Management System mode to accept it.
        """
        validate_max_current(amps)
        await self._unit.write_registers(
            SocketStatus.max_current.address, encode_float32(amps)
        )

    async def async_write_charging_phases(self, phases: ChargingPhases) -> None:
        """Switch the socket between one- and three-phase charging."""
        await self._unit.write_register(
            SocketStatus.charging_phases.address, int(phases)
        )


class Socket(ComponentGroup):
    """One socket of the station, polled as a whole."""

    def __init__(self, unit: ModbusUnit, number: int) -> None:
        self.number = number
        self.meter = SocketMeter(unit)
        self.status = SocketStatus(unit)
        super().__init__(unit, (self.meter, self.status))
