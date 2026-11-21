#!/usr/bin/env python3

"""Query an Alfen charging station; writes nothing unless --hold."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from time import monotonic
from typing import Any

from modbus_connection import ModbusConnection, ModbusError
from modbus_connection.cli_helper import (
    add_connection_args,
    connect_from_args,
    print_component,
)
from modbus_connection.model import Component, RegisterField

from alfen_modbus import (
    STATION_UNIT,
    AlfenCharger,
    Pollable,
    PollReport,
    Socket,
    SocketMeter,
    SocketStatus,
    StationIdentity,
    StationStatus,
)

# The station speaks Modbus TCP only.
CONNECTIONS = (("tcp", None),)

# The most registers one Modbus read may ask for.
MAX_COUNT = 125

# Strings that come out whole only under the right addressing.
ADDRESS_PROBES: tuple[tuple[int, RegisterField[str]], ...] = (
    (STATION_UNIT, StationIdentity.name),
    (STATION_UNIT, StationIdentity.serial_number),
    (STATION_UNIT, StationIdentity.platform_type),
)

# 32-bit values that turn absurd in the wrong word order.
WORD_ORDER_PROBES: tuple[tuple[int, RegisterField[Any]], ...] = (
    (STATION_UNIT, StationStatus.active_max_current),
    (STATION_UNIT, StationStatus.temperature),
    (1, SocketMeter.voltage_l1_n),
    (1, SocketMeter.frequency),
    (1, SocketStatus.max_current_valid_time),
    (1, SocketStatus.safe_current),
)

# Stop watching a released setpoint that never counts down to zero.
LAPSE_LIMIT = 600

EXAMPLES = """examples:
  uv run script/query.py 192.168.1.60
  # every register read, as JSON, for an issue report
  uv run script/query.py 192.168.1.60 --raw
  # settle addressing and word order; needs no working setup
  uv run script/query.py 192.168.1.60 --probe
  # any range, undecoded: unit 200, registers 1100 to 1105
  uv run script/query.py 192.168.1.60 --registers 200:1100:6
  # WRITES: hold 10 A on socket 1 for 3 minutes, then let it lapse
  uv run script/query.py 192.168.1.60 --hold 10 --seconds 180
"""


def _error(err: ModbusError) -> str:
    # A timeout arrives with no message of its own.
    return str(err) or type(err).__name__


def _register_range(text: str) -> tuple[int, int, int]:
    """Parse ``UNIT:ADDR[:COUNT]`` for ``--registers``."""
    match [int(part, 0) for part in text.split(":")]:
        case [unit, address]:
            return unit, address, 1
        case [unit, address, count] if 1 <= count <= MAX_COUNT:
            return unit, address, count
    raise argparse.ArgumentTypeError(
        f"{text!r} must be UNIT:ADDR[:COUNT], reading 1 to {MAX_COUNT} registers"
    )


def _title(component: Pollable) -> str:
    # Both sockets share a class, so only their number tells them apart.
    if isinstance(component, Socket):
        return f"socket {component.number}"
    return type(component).__name__


def _socket_components(socket: Socket | None) -> list[tuple[str, Component]]:
    if socket is None:
        return []
    return [
        (f"socket {socket.number} meter", socket.meter),
        (f"socket {socket.number} status", socket.status),
    ]


def _components(charger: AlfenCharger) -> list[tuple[str, Component]]:
    """Every component the station serves, in register order."""
    station: list[tuple[str, Component]] = [
        ("identity", charger.identity),
        ("clock", charger.clock),
        ("status", charger.status),
    ]
    if charger.scn is not None:
        station.append(("smart charging network", charger.scn))
    return [
        *station,
        *_socket_components(charger.socket_1),
        *_socket_components(charger.socket_2),
    ]


def _print_failures(report: PollReport) -> None:
    for failed, error in report.failed.items():
        print(f"  read {_title(failed)}: {_error(error)}")
    for failed, error in report.rewrite_failed.items():
        print(f"  resend {_title(failed)}: {_error(error)}")
    for taken in report.setpoint_taken_over:
        print(f"  another client took over {_title(taken)}; hold dropped")


async def _print_registers(
    connection: ModbusConnection, ranges: list[tuple[int, int, int]]
) -> None:
    for unit, address, count in ranges:
        try:
            words = await connection.for_unit(unit).read_holding_registers(
                address, count
            )
        except ModbusError as err:
            print(f"\nUnit {unit}, {address}+{count}: {_error(err)}")
            continue
        print(f"\nUnit {unit}, {address}+{count}")
        for offset, word in enumerate(words):
            print(f"  {address + offset:5}  0x{word:04X}  {word:5}")


async def _read_guesses(
    connection: ModbusConnection, unit: int, field: RegisterField[Any]
) -> tuple[tuple[str, list[int]], ...]:
    """Read a field as listed and one register lower, in one request."""
    print(f"\nUnit {unit}, {field.name} ({field.address})")
    try:
        words = await connection.for_unit(unit).read_holding_registers(
            field.address - 1, field.count + 1
        )
    except ModbusError as err:
        print(f"  {_error(err)}")
        return ()
    return ("as listed", words[1:]), ("one lower", words[:-1])


async def _probe(connection: ModbusConnection) -> None:
    """Decode known fields under each addressing and word order guess."""
    print("Addressing: the right guess shows each string whole, starting")
    print("with a text word; the wrong one drops two characters or")
    print("starts with a junk word such as 0xFFFF.")
    for unit, field in ADDRESS_PROBES:
        for label, words in await _read_guesses(connection, unit, field):
            print(
                f"  {label}  {field.decode(words)!r}"
                f"  (starts 0x{words[0]:04X} 0x{words[1]:04X})"
            )
    print("\nWord order: under the right addressing, one of the two")
    print("orders gives a plausible value and the other an absurd one.")
    for unit, field in WORD_ORDER_PROBES:
        for label, words in await _read_guesses(connection, unit, field):
            print(f"  {label}, high word first  {field.decode(words)!r}")
            print(f"  {label}, low word first   {field.decode(words[::-1])!r}")


async def _query(charger: AlfenCharger, raw: bool) -> None:
    """Read every component once and print it."""
    report = await charger.async_update(charger.components)
    # Per component: both sockets serve the same addresses.
    dump = (
        {
            title: await component.async_read_raw(notify=False)
            for title, component in _components(charger)
        }
        if raw
        else None
    )
    for title, component in _components(charger):
        print()
        print_component(component, title=title)
    print(f"\nLocal time      {charger.clock.local_time}")
    if not report.complete:
        print("\nFailed:")
        _print_failures(report)
    if dump is not None:
        print("\nRaw registers")
        print(json.dumps(dump, indent=2))


def _print_setpoint(started: float, status: SocketStatus, renewed: bool) -> None:
    print(
        f"  {monotonic() - started:6.0f} s"
        f"  max {status.max_current} A"
        f"  left {status.max_current_valid_time} s"
        f"  applied {status.actual_max_current} A"
        f"  safe {status.safe_current} A"
        f"  accounted for {status.setpoint_accounted_for}"
        f"{'  renewed' if renewed else ''}"
    )


async def _poll_setpoint(
    charger: AlfenCharger, socket: Socket, started: float, left: int | None
) -> int | None:
    """Poll one socket, print its setpoint, return the time it has left."""
    _print_failures(await charger.async_update((socket,)))
    now_left = socket.status.max_current_valid_time
    # A write resets the remaining time, so a rise means it was resent.
    _print_setpoint(
        started,
        socket.status,
        left is not None and now_left is not None and now_left > left,
    )
    return now_left


async def _hold(
    charger: AlfenCharger, socket: Socket, amps: float, seconds: int, interval: int
) -> None:
    """Hold a max current, then release it and watch it lapse."""
    started = monotonic()
    await socket.status.async_hold_max_current(amps)
    print(f"Holding {amps} A on socket {socket.number} for {seconds} s")
    left = await _poll_setpoint(charger, socket, started, None)
    while monotonic() - started < seconds:
        await asyncio.sleep(interval)
        left = await _poll_setpoint(charger, socket, started, left)
    socket.status.release_max_current()
    print("Released; watching it lapse to the safe current")
    released = monotonic()
    while left != 0 and monotonic() - released < LAPSE_LIMIT:
        await asyncio.sleep(interval)
        left = await _poll_setpoint(charger, socket, started, left)


async def main() -> int:
    """Read one station and print it."""
    # Silence the backend's connect traceback; one line below says it.
    logging.getLogger().addHandler(logging.NullHandler())

    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_connection_args(parser, connections=CONNECTIONS)
    parser.add_argument(
        "--raw", action="store_true", help="also dump every register read, as JSON"
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="decode known fields under each addressing and word order guess",
    )
    parser.add_argument(
        "--registers",
        type=_register_range,
        action="append",
        default=[],
        metavar="UNIT:ADDR[:COUNT]",
        help=f"also read these holding registers, up to {MAX_COUNT} at a time",
    )
    parser.add_argument(
        "--hold",
        type=float,
        metavar="AMPS",
        help="WRITES: hold this max current, then release it",
    )
    parser.add_argument(
        "--socket", type=int, choices=(1, 2), default=1, help="socket for --hold"
    )
    parser.add_argument(
        "--seconds", type=int, default=150, help="how long --hold holds"
    )
    parser.add_argument(
        "--interval", type=int, default=10, help="seconds between --hold polls"
    )
    args = parser.parse_args()
    if args.probe and (args.raw or args.hold is not None):
        parser.error("--probe needs no setup; drop --raw and --hold")

    try:
        connection = await connect_from_args(args)
    except ModbusError as err:
        print(f"Could not connect to {args.target}: {_error(err)}")
        return 1

    try:
        if args.probe:
            await _probe(connection)
            await _print_registers(connection, args.registers)
            return 0
        charger = AlfenCharger(connection.for_unit)
        try:
            await charger.async_setup()
        except ModbusError as err:
            print(f"Could not set the station up: {_error(err)}")
            print("--probe reads it without a working register map.")
            await _print_registers(connection, args.registers)
            return 1
        if args.hold is None:
            await _query(charger, args.raw)
        else:
            socket = charger.socket_1 if args.socket == 1 else charger.socket_2
            if socket is None:
                print(f"The station has no socket {args.socket}")
                return 1
            await _hold(charger, socket, args.hold, args.seconds, args.interval)
        await _print_registers(connection, args.registers)
    except ModbusError as err:
        print(f"The station failed: {_error(err)}")
        return 1
    finally:
        await connection.close()
    return 0


raise SystemExit(asyncio.run(main()))
