# alfen-modbus

Read and control Alfen EV charging stations over Modbus TCP, as typed Python
objects rather than register numbers.

The library maps Alfen's register set onto
[modbus-connection](https://github.com/home-assistant-libs/modbus-connection)'s device model:
you hand it a connection's `for_unit`, call `async_setup()` and `async_update()`,
and read sub-systems as attributes. It was written to back a Home Assistant
integration but works from any asyncio code. Fully typed (`mypy --strict`, ships
`py.typed`) and tested to 100% branch coverage.

> [!WARNING]
> Alpha. The register map comes from Alfen's documentation and has not yet been
> checked against a live station. See [Open questions](#open-questions).

## Supported stations

**`AlfenCharger`** reads the NG9xx platform: the Eve Single, Eve Double and Eve
Single Pro-line. Station-wide registers sit on unit 200 (`STATION_UNIT`), each
socket's on unit 1 or 2. `async_setup()` reads the identity and settles what the
station has: the socket count from its status block, and whether it serves the
Smart Charging Network (SCN) block. A single-socket station never sees a socket 2
read, and a station refusing the SCN block never sees it polled.

**Own an Alfen station?** The register map has not met real hardware yet, so
the most useful thing you can do is
[open an issue](https://github.com/darkrain-nl/alfen-modbus/issues) with the
output of `uv run script/query.py <address> --raw` (see [Checking a real
station](#checking-a-real-station)). It only reads, and its raw dump is enough
to settle the [open questions](#open-questions).

## Station setup

The station only answers once Modbus is switched on in the ACE Service Installer:

- **Active Load Balancing** must be licensed and enabled, with *Energy Management
  System* as its data source, for the station to accept a max current.
- **Allow reading** turns on reads; **Allow writing maximum currents** turns on
  the setpoints. Both are off by default.
- **Validity time** (default 60 s) is how long a written max current holds before
  the station falls back to its safe current. Rewrite the setpoint more often.

The station accepts two Modbus TCP clients at once, on port 502 (`MODBUS_PORT`).

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
the sockets passes `charger.sockets` and skips the rest.
`charger.components` lists every sub-system the station has. Both raise until
`async_setup()` has run, since only setup knows about socket 2 and the SCN.

The returned `PollReport` is keyed by those same objects: `updated` holds the
ones that refreshed and `failed` maps the rest to their error. A lost connection
raises, and so does a timeout before anything has answered.

```python
report = await charger.async_update(charger.components)
for component, error in report.failed.items():
    print(f"{component} kept its previous values: {error}")
```

To check which station answers at an address before setting one up, as a
config flow does, `await StationIdentity.async_detect(unit)` reads the identity
over a unit on `STATION_UNIT` alone.

### What it covers

| Object | Unit | Registers |
| :--- | :--- | :--- |
| `charger.identity` | 200 | Name, manufacturer, firmware, platform, serial (read once) |
| `charger.clock` | 200 | Station clock, UTC offset, uptime |
| `charger.status` | 200 | Active max current, board temperature, backoffice link, socket count |
| `charger.scn` | 200 | Smart Charging Network per-phase budget; `None` if refused |
| `charger.socket_1`, `charger.socket_2` | 1, 2 | Energy meter and charging state; `socket_2` is `None` on a single socket |

### Missing values

A value the station reports as NaN decodes to `None`. Stations with a Reallin
meter (built from 2021) leave four sums NaN: `current_sum`,
`apparent_power_sum`, `reactive_power_sum` and `real_energy_consumed_sum`.
Older ABB meters fill them all.

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

### Holding a setpoint

A written max current only lasts the validity time set in ACE Service
Installer (60 s by default); then the station falls back to its safe current.
No register reports the validity time itself, only how much of it is left. To
keep a setpoint in force, hold it instead of writing it:

```python
await charger.socket_1.status.async_hold_max_current(16.0)
# ... every poll now resends it in time
charger.socket_1.status.release_max_current()  # lapses to the safe current
```

Each `async_update()` that polls a socket with a held setpoint decides from
what it just read:

- **Resend** when the time left is at most twice the gap since the previous
  poll, so one more poll would still arrive before the setpoint lapses. A
  reboot or lapse leaves 0 s, so it resends then too. Any poll interval works,
  provided it is shorter than the validity time; with 10 s polls and the
  default 60 s, that is one write about every 40 s.
- **Step back** when the station reports a different max current with time
  still left: another Modbus client wrote it. The hold is dropped and the
  socket listed in the report's `setpoint_taken_over`. A second client
  writing the same value cannot be told apart, and does no harm.
- **Do nothing** otherwise.

Holding the value already held writes nothing, so an automation can set it as
often as it likes. A new value is written at once, with no rate limit: smooth
fast changes in the caller. A write updates the cached `max_current` straight
away, so a UI can show it without another poll. If a hold's write fails, the
hold stays and the next poll retries it. A failed resend is listed in the
report's `rewrite_failed`, apart from the reads in `failed`.

A hold lives in memory. After a restart nothing is resent, and the station
falls back to its safe current once the validity time runs out.

Only polled components are resent, so poll `charger.held_components` (or all
of `charger.components`) as well as whatever else you read.
`held_max_current` is the value held, or `None`. The SCN has the same for its
three phases: `async_hold_max_currents(l1, l2, l3)`, `held_max_currents` and
`release_max_currents()`.

## Public API

The public API is every name `alfen_modbus` exports in its `__all__`. Import
from there and nothing else: the submodules, and any name starting with an
underscore, are internal and can move in any release.

From 1.0 on, releases follow [semantic versioning](https://semver.org): a
change that breaks the public API needs a major version.

## Checking a real station

`script/query.py` reads one station once and prints every value it serves,
which is the quickest way to see whether a station is reachable, has Modbus
switched on, and is detected with the sockets you expect. It writes nothing
unless given `--hold`.

```bash
# every value, per component
uv run script/query.py 192.168.1.60
# every register read as well, undecoded, for an issue report
uv run script/query.py 192.168.1.60 --raw
# settle the open questions; needs no working register map
uv run script/query.py 192.168.1.60 --probe
# any range, undecoded: unit 200, registers 1100 to 1105
uv run script/query.py 192.168.1.60 --registers 200:1100:6
# WRITES: hold 10 A on socket 1 for 3 minutes, then let it lapse
uv run script/query.py 192.168.1.60 --hold 10 --seconds 180
```

A station that answers nothing at all may still have **Allow reading** off
(see [Station setup](#station-setup)). Components that failed are listed after
the values, with their error. `--raw` adds every register it read as
JSON, which is the most helpful thing to include in an issue about a wrong
value. `--registers UNIT:ADDR[:COUNT]` reads any range, up to 125 registers,
and still runs when setup fails.

`--hold AMPS` needs **Allow writing maximum currents**. It holds the max current
on `--socket` (1 by default) for `--seconds`, polling every `--interval`
(10 s), then releases it and keeps polling until the station falls back to its
safe current. Each line shows the setpoint, the time it has left, the applied
current and whether the station accounted for it; `renewed` marks a resend.

## Open questions

The guide and a working third-party integration disagree on two points, and the
library follows the integration until a live station settles it:

- **Addressing.** The guide says to subtract 1 from each register number; the
  library reads them as listed (300 is address 300).
- **Word order.** The guide says 32-bit values send the low word first; the
  library reads the high word first.

`query.py --probe` settles both in one run, without the library's map having
to work: it reads known strings and numbers both one register lower and as
listed, and decodes each number in both word orders. Addressing comes first,
from the strings: under the wrong guess a string loses its first two
characters or starts with a junk word. A float whose low word is zero, such as
16.0, can read right under both guesses, so only the strings decide it. Under
the right addressing, one word order gives plausible numbers and the other
absurd ones.

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
