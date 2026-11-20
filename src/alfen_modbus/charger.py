"""The top-level object for an Alfen charging station."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Final

from modbus_connection import (
    ModbusConnectionError,
    ModbusError,
    ModbusTimeoutError,
    ModbusUnit,
)
from modbus_connection.model import Component, ComponentGroup, read_optional

from .scn import SmartChargingNetwork
from .sockets import Socket
from .station import StationClock, StationIdentity, StationStatus

STATION_UNIT: Final = 200
"""The Modbus unit serving the station-wide registers."""

type Pollable = Component | ComponentGroup


@dataclass
class PollReport:
    """What one poll managed to refresh."""

    updated: set[Pollable] = field(default_factory=set)
    failed: dict[Pollable, ModbusError] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """Whether every polled sub-system refreshed."""
        return not self.failed


class AlfenCharger:
    """An Alfen charging station and its one or two sockets."""

    def __init__(self, for_unit: Callable[[int], ModbusUnit]) -> None:
        self._for_unit = for_unit
        self._station = for_unit(STATION_UNIT)
        self._setup_done = False
        self.identity = StationIdentity(self._station)
        self.clock = StationClock(self._station)
        self.status = StationStatus(self._station)
        self.scn: SmartChargingNetwork | None = None
        self.socket_1 = Socket(for_unit(1), 1)
        self.socket_2: Socket | None = None

    async def async_setup(self) -> None:
        """Read the identity once and settle which sub-systems exist."""
        if self._setup_done:
            return
        await self.identity.async_update()
        await self.status.async_update()
        if self.status.socket_count == 2:
            self.socket_2 = Socket(self._for_unit(2), 2)
        self.scn = await read_optional(SmartChargingNetwork(self._station))
        # Set last, so a failed run is retried on the next call.
        self._setup_done = True

    @property
    def components(self) -> tuple[Pollable, ...]:
        """Every sub-system the station has, to pass to a poll."""
        # Before setup, socket 2 and the SCN would be missing silently.
        if not self._setup_done:
            raise RuntimeError("components are unknown until async_setup()")
        return tuple(
            component
            for component in (
                self.clock,
                self.status,
                self.scn,
                self.socket_1,
                self.socket_2,
            )
            if component is not None
        )

    async def async_update(self, components: Iterable[Pollable]) -> PollReport:
        """Poll each sub-system on its own and record what happened."""
        await self.async_setup()
        report = PollReport()
        for component in components:
            try:
                await component.async_update(notify=False)
            # A dead link fails every read, so the rest would only wait.
            except ModbusConnectionError:
                raise
            # Silence before any answer is the station, not one sub-system.
            except ModbusTimeoutError as err:
                if not report.updated and not report.failed:
                    raise
                report.failed[component] = err
            except ModbusError as err:
                report.failed[component] = err
            else:
                report.updated.add(component)
        # Listeners fire once every read is in, so they see one poll.
        for component in report.updated:
            component.notify()
        return report
