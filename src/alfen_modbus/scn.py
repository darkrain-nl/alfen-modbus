"""Smart Charging Network registers, served on Modbus unit 200."""

from __future__ import annotations

from modbus_connection import ModbusUnit
from modbus_connection.encode import encode_float32
from modbus_connection.model import Component, boolean, float32, integer, string, uint32

from .model import NAN, HoldCheck, SetpointHold, validate_max_current


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
        # The station reads a write back as is, so show it before a poll.
        for field, amps in (
            (SmartChargingNetwork.max_current_l1, l1),
            (SmartChargingNetwork.max_current_l2, l2),
            (SmartChargingNetwork.max_current_l3, l3),
        ):
            self._values[field.name] = amps

    async def async_hold_max_currents(self, l1: float, l2: float, l3: float) -> None:
        """Set each phase's max current and keep them in force."""
        # The same value again writes nothing, unless it never got through.
        if (
            self._hold is not None
            and self._hold.sent
            and self._hold.amps == (l1, l2, l3)
        ):
            return
        # Held before the write, so a failed write is retried next poll.
        hold = SetpointHold((l1, l2, l3))
        self._hold = hold
        await self.async_write_max_currents(l1, l2, l3)
        hold.sent = True

    def release_max_currents(self) -> None:
        """Stop resending, so each phase lapses to the safe current."""
        self._hold = None

    async def async_keep_hold(self) -> HoldCheck:
        """Resend the held max currents if due; drop them if taken over."""
        if self._hold is None:
            return HoldCheck.KEEP
        hold = self._hold
        check = hold.check(
            (self.max_current_l1, self.max_current_l2, self.max_current_l3),
            (self.valid_time_l1, self.valid_time_l2, self.valid_time_l3),
        )
        if check is HoldCheck.TAKEN_OVER:
            self._hold = None
        elif check is HoldCheck.RESEND:
            await self.async_write_max_currents(*hold.amps)
            hold.sent = True
        return check
