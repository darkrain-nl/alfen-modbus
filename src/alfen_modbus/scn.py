"""Smart Charging Network registers, served on Modbus unit 200."""

from __future__ import annotations

from modbus_connection import ModbusUnit
from modbus_connection.encode import encode_float32
from modbus_connection.model import Component, boolean, float32, integer, string, uint32

from .enums import Phase
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
        self._holds: dict[Phase, SetpointHold] = {}

    def held_max_current(self, phase: Phase) -> float | None:
        """The phase's max current kept in force, or ``None``."""
        hold = self._holds.get(phase)
        return None if hold is None else hold.amps

    async def async_write_max_current(self, phase: Phase, amps: float) -> None:
        """Set one phase's max current, restarting its validity timer.

        The station needs Energy Management System mode to accept it.
        """
        validate_max_current(amps)
        await self._unit.write_registers(
            _MAX_CURRENT[phase].address, encode_float32(amps)
        )
        # The station reads a write back as is, so show it before a poll.
        self._values[_MAX_CURRENT[phase].name] = amps

    async def async_hold_max_current(self, phase: Phase, amps: float) -> None:
        """Set one phase's max current and keep it in force."""
        held = self._holds.get(phase)
        # The same value again writes nothing, unless it never got through.
        if held is not None and held.sent and held.amps == amps:
            return
        # Held before the write, so a failed write is retried next poll.
        hold = SetpointHold(amps)
        self._holds[phase] = hold
        await self.async_write_max_current(phase, amps)
        hold.sent = True

    def release_max_current(self, phase: Phase) -> None:
        """Stop resending one phase, so it lapses to the safe current."""
        self._holds.pop(phase, None)

    async def async_keep_hold(self) -> HoldCheck:
        """Resend each held phase if due; drop a phase taken over."""
        readings = {
            Phase.L1: (self.max_current_l1, self.valid_time_l1),
            Phase.L2: (self.max_current_l2, self.valid_time_l2),
            Phase.L3: (self.max_current_l3, self.valid_time_l3),
        }
        checks: set[HoldCheck] = set()
        for phase, hold in list(self._holds.items()):
            check = hold.check(*readings[phase])
            if check is HoldCheck.TAKEN_OVER:
                del self._holds[phase]
            elif check is HoldCheck.RESEND:
                await self.async_write_max_current(phase, hold.amps)
                hold.sent = True
            checks.add(check)
        # A takeover outranks a resend in what the poll reports.
        if HoldCheck.TAKEN_OVER in checks:
            return HoldCheck.TAKEN_OVER
        if HoldCheck.RESEND in checks:
            return HoldCheck.RESEND
        return HoldCheck.KEEP


_MAX_CURRENT = {
    Phase.L1: SmartChargingNetwork.max_current_l1,
    Phase.L2: SmartChargingNetwork.max_current_l2,
    Phase.L3: SmartChargingNetwork.max_current_l3,
}
