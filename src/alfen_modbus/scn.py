"""Smart Charging Network registers, served on Modbus unit 200."""

from __future__ import annotations

from modbus_connection import ModbusUnit
from modbus_connection.encode import encode_float32
from modbus_connection.model import Component, boolean, float32, integer, string, uint32

from .model import NAN, SetpointHold, validate_max_current


class SmartChargingNetwork(Component):
    """Per-phase budget of the Smart Charging Network as one entity."""

    name = string(1400, 4)
    socket_count = integer(1404, signed=False, nan=NAN)
    consumption_l1 = float32(1405, unit="A")
    consumption_l2 = float32(1407, unit="A")
    consumption_l3 = float32(1409, unit="A")
    actual_max_current_l1 = float32(1411, unit="A")
    actual_max_current_l2 = float32(1413, unit="A")
    actual_max_current_l3 = float32(1415, unit="A")
    max_current_l1 = float32(1417, unit="A")
    max_current_l2 = float32(1419, unit="A")
    max_current_l3 = float32(1421, unit="A")
    valid_time_l1 = uint32(1423, unit="s")
    valid_time_l2 = uint32(1425, unit="s")
    valid_time_l3 = uint32(1427, unit="s")
    safe_current = float32(1429, unit="A")
    max_current_enabled = boolean(1431, nan=NAN)

    def __init__(self, unit: ModbusUnit) -> None:
        super().__init__(unit)
        self._hold: SetpointHold | None = None

    @property
    def held_max_currents(self) -> tuple[float, ...] | None:
        """The per-phase max currents kept in force, or ``None``."""
        return None if self._hold is None else self._hold.amps

    async def async_write_max_currents(self, l1: float, l2: float, l3: float) -> None:
        """Set each phase's max current, restarting its validity timer.

        The station needs Energy Management System mode to accept it.
        """
        for amps in (l1, l2, l3):
            validate_max_current(amps)
        await self._unit.write_registers(
            SmartChargingNetwork.max_current_l1.address,
            [*encode_float32(l1), *encode_float32(l2), *encode_float32(l3)],
        )

    async def async_hold_max_currents(self, l1: float, l2: float, l3: float) -> None:
        """Set each phase's max current and keep them in force.

        The hold stays when this write fails, so the next poll retries.
        """
        self._hold = SetpointHold((l1, l2, l3))
        await self.async_write_max_currents(l1, l2, l3)

    def release_max_currents(self) -> None:
        """Stop resending, so each phase lapses to the safe current."""
        self._hold = None

    async def async_keep_hold(self) -> None:
        """Send the held max currents again if one is about to lapse."""
        if self._hold is None or not self._hold.due(
            (self.max_current_l1, self.max_current_l2, self.max_current_l3),
            (self.valid_time_l1, self.valid_time_l2, self.valid_time_l3),
        ):
            return
        await self.async_write_max_currents(*self._hold.amps)
