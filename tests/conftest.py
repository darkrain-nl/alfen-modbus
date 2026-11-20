"""Fixtures: an Alfen station on modbus-connection's mock backend."""

from __future__ import annotations

import pytest
from modbus_connection.encode import encode_float32, encode_float64
from modbus_connection.mock import MockModbusConnection, MockModbusUnit

NAN_32 = [0x7FC0, 0x0000]
NAN_64 = [0x7FF8, 0x0000, 0x0000, 0x0000]


def ascii_words(text: str, length: int) -> list[int]:
    """Pack ASCII into ``length`` registers, two characters each."""
    padded = text.ljust(length * 2, "\0")[: length * 2].encode("ascii")
    return [int.from_bytes(padded[i : i + 2]) for i in range(0, length * 2, 2)]


STATION: dict[int, int | list[int]] = {
    100: ascii_words("ALF_1000", 17),
    117: ascii_words("Alfen B.V.", 5),
    122: 1,
    123: ascii_words("6.4.0-4210", 17),
    140: ascii_words("NG920", 17),
    157: ascii_words("ACE0012345", 11),
    # 2026-11-20 14:30:05, UTC+60 min
    168: [2026, 11, 20, 14, 30, 5],
    174: [0, 0, 0x0001, 0x86A0],  # 100000 ms
    178: 60,
    1100: encode_float32(16.0),
    1102: encode_float32(38.5),
    1104: 1,
    1105: 2,
    1400: ascii_words("ALF_SCN", 4),
    1404: 2,
    1405: [
        *encode_float32(10.5),
        *encode_float32(11.0),
        *encode_float32(9.5),
    ],
    1411: [*encode_float32(25.0), *encode_float32(25.0), *encode_float32(25.0)],
    1417: [*encode_float32(20.0), *encode_float32(20.0), *encode_float32(20.0)],
    1423: [0, 45, 0, 46, 0, 47],
    1429: encode_float32(6.0),
    1431: 1,
}

SOCKET: dict[int, int | list[int]] = {
    300: 0x03,  # initialised | updated
    301: [0, 0, 0, 1500],  # 1.5 s
    305: 0,  # RTU
    306: [*encode_float32(230.1), *encode_float32(231.2), *encode_float32(229.8)],
    312: [*encode_float32(400.0), *encode_float32(401.0), *encode_float32(399.0)],
    318: [*encode_float32(0.2), *encode_float32(16.0), *encode_float32(15.9)],
    324: [*encode_float32(16.1), *NAN_32],
    328: [*encode_float32(0.99)] * 4,
    336: encode_float32(50.01),
    338: [*encode_float32(3680.0)] * 4,
    346: [*encode_float32(3700.0), *encode_float32(3700.0)],
    350: [*encode_float32(3700.0), *NAN_32],
    354: [*encode_float32(-12.5)] * 4,
    362: [*encode_float64(1234567.5)] * 4,
    378: [*encode_float64(0.0), *encode_float64(0.0)],
    386: [*encode_float64(0.0), *NAN_64],
    394: [*encode_float64(2000000.0)] * 4,
    410: [*encode_float64(1500.0)] * 4,
    1200: 1,
    1201: ascii_words("C2", 5),
    1206: encode_float32(16.0),
    1208: [0, 42],
    1210: encode_float32(16.0),
    1212: encode_float32(6.0),
    1214: 1,
    1215: 3,
}


@pytest.fixture
def connection() -> MockModbusConnection:
    """A dual-socket station that serves the Smart Charging Network."""
    connection = MockModbusConnection()
    connection.for_unit(200).holding.update(STATION)
    connection.for_unit(1).holding.update(SOCKET)
    connection.for_unit(2).holding.update(SOCKET)
    return connection


@pytest.fixture
def station(connection: MockModbusConnection) -> MockModbusUnit:
    """The unit serving the station-wide registers."""
    return connection.for_unit(200)


@pytest.fixture
def socket_1(connection: MockModbusConnection) -> MockModbusUnit:
    """The unit serving the first socket."""
    return connection.for_unit(1)
