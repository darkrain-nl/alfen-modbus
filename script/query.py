#!/usr/bin/env python3

"""Query an Alfen charging station and print every value; writes nothing."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from modbus_connection import ModbusError
from modbus_connection.cli_helper import (
    add_connection_args,
    connect_from_args,
    print_component,
)
from modbus_connection.model import Component

from alfen_modbus import AlfenCharger, Socket

# The station speaks Modbus TCP only.
CONNECTIONS = (("tcp", None),)

EXAMPLES = """examples:
  uv run script/query.py 192.168.1.60
  # every register read, as JSON, for an issue report
  uv run script/query.py 192.168.1.60 --raw
"""


def _error(err: ModbusError) -> str:
    # A timeout arrives with no message of its own.
    return str(err) or type(err).__name__


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
    args = parser.parse_args()

    try:
        connection = await connect_from_args(args)
    except ModbusError as err:
        print(f"Could not connect to {args.target}: {_error(err)}")
        return 1

    charger = AlfenCharger(connection)
    try:
        report = await charger.async_update()
        # Per component: both sockets serve the same addresses.
        raw = (
            {
                title: await component.async_read_raw(notify=False)
                for title, component in _components(charger)
            }
            if args.raw
            else None
        )
    except ModbusError as err:
        print(f"Could not read the station: {_error(err)}")
        return 1
    finally:
        await connection.close()

    for title, component in _components(charger):
        print()
        print_component(component, title=title)
    print(f"\nLocal time      {charger.clock.local_time}")
    if report.failed:
        print("\nFailed to read:")
        for name, error in report.failed.items():
            print(f"  {name}: {_error(error)}")
    if raw is not None:
        print("\nRaw registers")
        print(json.dumps(raw, indent=2))
    return 0


raise SystemExit(asyncio.run(main()))
