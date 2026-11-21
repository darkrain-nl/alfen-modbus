"""The top-level object for an Alfen charging station."""

from __future__ import annotations

import logging
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

from .model import HoldCheck
from .scn import SmartChargingNetwork
from .sockets import Socket, SocketStatus
from .station import StationClock, StationIdentity, StationStatus

MODBUS_PORT: Final = 502
"""The Modbus TCP port; the station serves no other."""

STATION_UNIT: Final = 200
"""The Modbus unit serving the station-wide registers."""

type Pollable = Component | ComponentGroup

_LOGGER = logging.getLogger(__name__)


@dataclass
class PollReport:
    """What one poll managed to refresh."""

    updated: set[Pollable] = field(default_factory=set)
    failed: dict[Pollable, ModbusError] = field(default_factory=dict)
    rewrite_failed: dict[Pollable, ModbusError] = field(default_factory=dict)
    setpoint_taken_over: set[Pollable] = field(default_factory=set)

    @property
    def complete(self) -> bool:
        """Whether every polled sub-system refreshed and kept its hold."""
        return not self.failed and not self.rewrite_failed


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
        _LOGGER.debug(
            "%s is a %s on firmware %s, with %s socket(s); SCN %s",
            self.identity.serial_number,
            self.identity.platform_type,
            self.identity.firmware_version,
            self.status.socket_count,
            "refused" if self.scn is None else "served",
        )
        # Set last, so a failed run is retried on the next call.
        self._setup_done = True

    @property
    def sockets(self) -> tuple[Socket, ...]:
        """The station's one or two sockets."""
        # Before setup, socket 2 would be missing silently.
        if not self._setup_done:
            raise RuntimeError("sockets are unknown until async_setup()")
        return tuple(
            socket for socket in (self.socket_1, self.socket_2) if socket is not None
        )

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

    @property
    def held_components(self) -> tuple[Pollable, ...]:
        """The sub-systems holding a setpoint, which each poll must read."""
        held: list[Pollable] = [
            socket
            for socket in self.sockets
            if socket.status.held_max_current is not None
        ]
        if self.scn is not None and self.scn.held_max_currents is not None:
            held.append(self.scn)
        return tuple(held)

    async def async_update(self, components: Iterable[Pollable]) -> PollReport:
        """Poll each sub-system, resend due setpoints, report the outcome."""
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
        # A held setpoint lapses to the safe current unless resent in time.
        for socket in self.sockets:
            if socket in report.updated:
                await self._async_keep_hold(
                    report, socket, socket.status, f"socket {socket.number}"
                )
        if self.scn is not None and self.scn in report.updated:
            await self._async_keep_hold(report, self.scn, self.scn, "the SCN")
        # Listeners fire once every read is in, so they see one poll.
        for component in report.updated:
            component.notify()
        return report

    async def _async_keep_hold(
        self,
        report: PollReport,
        component: Pollable,
        holder: SocketStatus | SmartChargingNetwork,
        label: str,
    ) -> None:
        """Resend one held setpoint if due, and record the outcome."""
        try:
            check = await holder.async_keep_hold()
        except ModbusConnectionError:
            raise
        except ModbusError as err:
            report.rewrite_failed[component] = err
            _LOGGER.debug(
                "%s: resending %s's held max current failed: %s",
                self.identity.serial_number,
                label,
                err,
            )
            return
        if check is HoldCheck.TAKEN_OVER:
            report.setpoint_taken_over.add(component)
            _LOGGER.debug(
                "%s: %s reads a max current we did not write, with time "
                "left; another client wrote it, so the hold is dropped",
                self.identity.serial_number,
                label,
            )
        elif check is HoldCheck.RESEND:
            _LOGGER.debug(
                "%s: resent %s's held max current before it lapsed",
                self.identity.serial_number,
                label,
            )
