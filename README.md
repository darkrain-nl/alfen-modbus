# alfen-modbus

Read and control Alfen EV charging stations (the NG9xx platform: Eve Single,
Eve Double, Eve Single Pro-line) over Modbus TCP, as typed Python objects.

Built on [modbus-connection](https://github.com/home-assistant-libs/modbus-connection),
written to back a Home Assistant integration but usable from any asyncio code.
Fully typed (`mypy --strict`, ships `py.typed`) and tested to 100% branch coverage.

> [!WARNING]
> Alpha. The register map comes from Alfen's documentation and has not yet been
> checked against a live station. See [Open questions](#open-questions).

## Station setup

The station only answers once Modbus is switched on in the ACE Service Installer:

- **Active Load Balancing** must be licensed and enabled, with *Energy Management
  System* as its data source, for the station to accept a max current.
- **Allow reading** turns on reads; **Allow writing maximum currents** turns on
  the setpoints. Both are off by default.
- **Validity time** (default 60 s) is how long a written max current holds before
  the station falls back to its safe current. Rewrite the setpoint more often.

The station accepts two Modbus TCP clients at once, on port 502. Station-wide
registers are on unit 200; each socket's are on unit 1 or 2.

## Usage

```python
import asyncio

from modbus_connection.tmodbus import connect_tcp

from alfen_modbus import AlfenCharger


async def main() -> None:
    connection = await connect_tcp("192.168.1.60")
    charger = AlfenCharger(connection.for_unit)
    try:
        await charger.async_setup()  # reads the identity, finds socket 2
        print(charger.identity.serial_number, charger.identity.platform_type)
        report = await charger.async_update(charger.components)
        print(charger.socket_1 in report.updated)
        status = charger.socket_1.status
        print(status.mode_3_state, status.actual_max_current)
        print(charger.socket_1.meter.real_power_sum)
        await status.async_write_max_current(10.0)
    finally:
        await connection.close()


asyncio.run(main())
```

`async_update()` polls the sub-systems you pass, so a caller that only needs
the sockets passes `(charger.socket_1, charger.socket_2)` and skips the rest.
`charger.components` lists every sub-system the station has; it raises until
`async_setup()` has run, since only setup knows about socket 2 and the SCN.

The returned `PollReport` is keyed by those same objects: `updated` holds the
ones that refreshed and `failed` maps the rest to their error. A lost connection
raises, and so does a timeout before anything has answered.

A value the station reports as NaN decodes to `None`.
Stations with a Reallin meter (built from 2021) leave four sums NaN:
`current_sum`, `apparent_power_sum`, `reactive_power_sum` and
`real_energy_consumed_sum`. Older ABB meters fill them all.

### What it covers

| Object | Unit | Registers |
| :--- | :--- | :--- |
| `charger.identity` | 200 | Name, manufacturer, firmware, platform, serial (read once) |
| `charger.clock` | 200 | Station clock, UTC offset, uptime |
| `charger.status` | 200 | Active max current, board temperature, backoffice link, socket count |
| `charger.scn` | 200 | Smart Charging Network per-phase budget; `None` if refused |
| `charger.socket_1`, `charger.socket_2` | 1, 2 | Energy meter and charging state; `socket_2` is `None` on a single socket |

### Writes

| Method | Effect |
| :--- | :--- |
| `SocketStatus.async_write_max_current(amps)` | The socket's max current |
| `SocketStatus.async_write_charging_phases(phases)` | Switch between 1 and 3 phases |
| `SmartChargingNetwork.async_write_max_currents(l1, l2, l3)` | The SCN's per-phase max currents |

Each max current must be 0 (pause) or at least `MIN_CHARGING_CURRENT` (6 A, the
IEC 61851 floor); anything in between raises `ValueError` before writing. The
library sets no upper bound: the station caps a setpoint at its installation
limit. Don't use `status.active_max_current` or `SocketStatus.actual_max_current`
as a UI ceiling either. Both follow the setpoint, so the slider would ratchet down.

## Open questions

The guide and a working third-party integration disagree on two points, and the
library follows the integration until a live station settles it:

- **Addressing.** The guide says to subtract 1 from each register number; the
  library reads them as listed (300 is address 300).
- **Word order.** The guide says 32-bit values send the low word first; the
  library reads the high word first.

`script/query.py` reads every register and writes nothing. Run it against a
station to check:

```bash
uv run script/query.py 192.168.1.60
```

## Register map source

Alfen *Modbus for ACE, Configuration Guide*, version 1.0 (May 2025), for ACE
Service Installer 3.4.10-130 and firmware 4.10. The guide gives no data types for
the station status block (1100 to 1105); those were settled from observed station
behaviour. Alfen's copyright forbids redistributing the guide, so it is not in
this repository.

## Development

```bash
uv sync
uv run pytest
uv run mypy
uv run ruff check .
uv run ruff format --check .
```

`pytest` fails below 100% branch coverage. See [RELEASING.md](RELEASING.md) for
publishing.

## License

[Apache-2.0](LICENSE).
