"""Station-wide registers, served on Modbus unit 200."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from modbus_connection.model import Component, boolean, float32, integer, string, uint64

from .model import NAN


class StationIdentity(Component):
    """Product identification of the charging station."""

    name = string(100, 17)
    manufacturer = string(117, 5)
    modbus_table_version = integer(122, nan=NAN)
    firmware_version = string(123, 17)
    platform_type = string(140, 17)
    serial_number = string(157, 11)


class StationClock(Component):
    """The station's local clock and uptime."""

    year = integer(168, nan=NAN)
    month = integer(169, nan=NAN)
    day = integer(170, nan=NAN)
    hour = integer(171, nan=NAN)
    minute = integer(172, nan=NAN)
    second = integer(173, nan=NAN)
    uptime = uint64(174, scale=0.001, unit="s")
    utc_offset = integer(178, nan=NAN, unit="min")

    @property
    def local_time(self) -> datetime | None:
        """The station's clock, aware of its UTC offset."""
        match (
            self.year,
            self.month,
            self.day,
            self.hour,
            self.minute,
            self.second,
            self.utc_offset,
        ):
            case (
                int() as year,
                int() as month,
                int() as day,
                int() as hour,
                int() as minute,
                int() as second,
                int() as offset,
            ):
                return datetime(
                    year,
                    month,
                    day,
                    hour,
                    minute,
                    second,
                    tzinfo=timezone(timedelta(minutes=offset)),
                )
        return None


class StationStatus(Component):
    """Live state of the station as a whole."""

    active_max_current = float32(1100, unit="A")
    temperature = float32(1102, unit="°C")
    backoffice_connected = boolean(1104, nan=NAN)
    socket_count = integer(1105, nan=NAN)
