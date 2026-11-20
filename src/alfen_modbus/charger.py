"""The top-level object for an Alfen charging station."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from modbus_connection import ModbusUnit
from modbus_connection.model import Device, UpdateReport, read_optional

from .scn import SmartChargingNetwork
from .sockets import Socket
from .station import StationClock, StationIdentity, StationStatus

STATION_UNIT: Final = 200
"""The Modbus unit serving the station-wide registers."""

# Every component a poll may read; setup leaves absent ones None.
READING_COMPONENTS: Final[tuple[str, ...]] = (
    "clock",
    "status",
    "scn",
    "socket_1",
    "socket_2",
)


class AlfenCharger(Device):
    """An Alfen charging station and its one or two sockets."""

    def __init__(self, for_unit: Callable[[int], ModbusUnit]) -> None:
        station = for_unit(STATION_UNIT)
        super().__init__(station)
        self._for_unit = for_unit
        self.identity = StationIdentity(station)
        self.clock = StationClock(station)
        self.status = StationStatus(station)
        self.scn: SmartChargingNetwork | None = None
        self.socket_1 = Socket(for_unit(1), 1)
        self.socket_2: Socket | None = None

    async def _async_setup(self) -> None:
        """Read the identity and settle which sub-systems exist."""
        await self.identity.async_update()
        await self.status.async_update()
        if self.status.socket_count == 2:
            self.socket_2 = Socket(self._for_unit(2), 2)
        self.scn = await read_optional(SmartChargingNetwork(self.modbus_unit))

    async def async_update(self) -> UpdateReport:
        """Poll every sub-system the station has."""
        return await self.async_poll(READING_COMPONENTS)
