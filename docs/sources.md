# Sources: serial devices, ZeroMQ, PLCs and APIs

A source feeds values to a dashboard. On the command line each has a flag
(`sightglass --help` lists them); in Python you pass sources to
`monitor.serve(sources=[...])` or `monitor.start(sources=[...])`. To describe
sources in a file instead, see [Dashboards from a config file](config.md).

## Read a serial device in Python

```python
from sightglass import CsvDecoder, Monitor, SerialSource

monitor = Monitor()
# The device prints one line per sample: "21.5,48,OK"
device = SerialSource(
    "auto", baudrate=115200, decoder=CsvDecoder(["temperature", "humidity", "status"])
)
monitor.serve(sources=[device])
```

## Every source

| Source | Reads |
| --- | --- |
| `SerialSource(port, baudrate=115200, decoder=...)` | a serial port (`"auto"` picks the first USB device); waits for it and reconnects |
| `ZmqSource(endpoint, pattern="sub")` | ZeroMQ PUB/SUB (a publisher that binds `endpoint`) |
| `ZmqSource(endpoint, pattern="pull")` | ZeroMQ PUSH/PULL: binds `endpoint`; unlike PUB/SUB, nothing sent before the monitor starts is lost |
| `AdsSource(target, variables)` | a Beckhoff TwinCAT PLC over ADS ([below](#beckhoff-twincat-plcs-ads)); can also write to it |
| `RestSource(*urls, interval=1)` | JSON REST endpoints, polled with GET ([below](#rest-apis)) |
| `InfluxSource(url, org=, token=, bucket=)` | an InfluxDB 2.x bucket, polled with Flux ([config file](config.md#influxdb) or Python) |
| `StdinSource()` | piped standard input, as `sightglass` does |
| `ProcessSource(interval=1)` | the program it runs in: CPU, memory, threads, open files, uptime ([below](#your-programs-own-cpu-and-memory)) |
| `SimulatedSource(fn, interval=0.05)` | `fn(seconds)` called every `interval` seconds, for demos and tests |

## Your program's own CPU and memory

```python
from sightglass import ProcessSource, start

monitor = start(sources=[ProcessSource()])
```

Once a second it sends, and shows under a `process` heading:

| Id | Value | Shown as |
| --- | --- | --- |
| `process.cpu` | percent of one core used since the last sample (over 100 with several threads busy) | a chart, `12.5 %` |
| `process.rss` | resident memory, in bytes | a chart, `74.1 MB` |
| `process.threads` | Python threads (`threading.active_count()`) | a number |
| `process.fds` | open files and sockets (handles on Windows) | a number |
| `process.uptime` | seconds since the process started | `125 s` |
| `process.gc_collections`, `process.gc_collected` | garbage collector runs, and the objects they freed, since the start | numbers |

Declare one of these ids yourself to show it your way; the source keeps
yours. `prefix="worker"` sends `worker.cpu` and so on, and `interval=` sets
the seconds between samples. CPU is left out of the first sample rather than
sent as 0: it is measured between samples.

It needs nothing installed. With [psutil](https://pypi.org/project/psutil/)
installed it reads memory, open files and uptime through psutil; without it
they come from the operating system. Two things need psutil: a count of open
files on Windows (without it, `process.fds` is not sent), and uptime outside
Linux (without it, counted from when sightglass was imported).

## Wire formats: decoders

Serial and ZeroMQ sources take a `decoder`, which turns the bytes they receive
into values.

| Decoder | Wire format |
| --- | --- |
| `KeyValueDecoder()` | `X.velocity 1.5`, or `key=value` pairs: `progress=5 status="two words"` |
| `CsvDecoder(fields)` | `1.5,-2.0,OK`, values in the order of `fields` |
| `JsonDecoder()` | `{"X": {"velocity": 1.5}}` per line; nesting addresses groups and fields |
| `BinaryFrameDecoder(names, scale=1000)` | the binary frame protocol below |

## Binary frames, for firmware where text is too slow

```
0xAA | id | type | value | n | param_1 ... param_n | 0xFF
```

- `type 0x01`: `value` is a little-endian int32, divided by `scale` (1000 by default).
- `type 0x02`: `value` is a NUL-terminated UTF-8 string (up to 255 bytes).
- `id` and the params are single bytes, translated through `names`
  (`{1: "X", 10: "velocity"}`). The update key is the id's name followed by the
  params' names, so id `X` with param `velocity` updates `"X.velocity"`.

Corrupt frames are skipped and decoding resumes at the next `0xAA`.

## REST APIs

`RestSource` needs the `rest` extra (`aiohttp`). It polls each URL with GET:

- **Ids:** nested objects become dotted ids, and lists of objects are keyed by
  their `id` field. `Endpoint(url, prefix=, select=, key=, interval=,
  accept=)` tunes one URL; `headers={...}` sends an API key or bearer token.
- **Invalid data is blanked:** `null`, or an endpoint that can't be reached,
  blanks that id (a gap in a plot) rather than leaving its last value showing.
- **A down endpoint** is logged once and retried.
- **Big replies:** one over 16 MB is refused (64 MB for InfluxDB), and one
  over 256 KB is parsed in a worker thread, so it doesn't hold up the page.

## Beckhoff TwinCAT PLCs (ADS)

Install the `ads` extra (`pip install "sightglass[web,ads]"`), then give
`sightglass` the PLC's AMS net id and the variables to show; `*` matches any
characters:

```bash
sightglass --ads 5.12.34.56.1.1 --vars 'MAIN.*,GVL.fTemperature'
```

All the variables are read in one ADS request every 0.1 s and shown under
their PLC names. Structs and arrays become one value per member
(`MAIN.stAxis.fPosition`, `GVL.aTemps[1]`), BOOLs become lamps, and the
variables are looked up again whenever a new program is downloaded.

For a lasting dashboard, describe the PLC in an interface file, with the
types pasted from the PLC project ([examples/plc.toml](https://github.com/davidson-engineering/sightglass/blob/main/examples/plc.toml) is
a commented one):

```toml
target = "5.12.34.56.1.1:851"
types = """
TYPE ST_Axis :
STRUCT
    fPosition : LREAL;
    bEnabled  : BOOL;
END_STRUCT
END_TYPE
"""

[variables]
"MAIN.stAxis1" = "ST_Axis"           # one value per member
"GVL.fTemperature" = {}              # the PLC says what it is
"MAIN.*" = {}                        # every plain top-level variable in MAIN
"GVL.nSetpoint" = { write = true }   # may be written: see below
```

`sightglass --ads plc.toml` reads it. `types` takes struct, enum and alias
declarations as TwinCAT writes them, comments, initial values and
`{attribute 'pack_mode' := '1'}` included. Structs are laid out in memory as
TwinCAT 3 does, and each variable's size is checked against the PLC's, so a
declaration that doesn't match is reported rather than shown as wrong values.
Declared enums are shown by name. From Python:

```python
from sightglass import AdsSource, Monitor

plc = AdsSource("5.12.34.56.1.1", ["MAIN.*", "GVL.fTemperature"])
# or AdsSource.from_file("plc.toml")
Monitor().serve(sources=[plc])
```

### Write to the PLC, behind four checks

Values sent to a variable marked `write = true` (with curl,
`Client` or `monitor.set`) are written to the PLC, and the dashboard shows
what the PLC reads back. Nothing is written unless all of these hold:

- the variable is marked `write = true`;
- `sightglass` was started with `--allow-writes` (`allow_writes=True`);
- the environment variable `SIGHTGLASS_ALLOW_WRITES` is `1`;
- the request has the dashboard's token: values for a PLC are refused over
  HTTP from a dashboard without `--token`.

```bash
export SIGHTGLASS_ALLOW_WRITES=1
sightglass --ads plc.toml --allow-writes --token change-me
curl -H 'Authorization: Bearer change-me' -d 'GVL.nSetpoint=75' http://127.0.0.1:8080/update
```

Each value is checked against its type, then against the type the PLC itself
gives the variable, and a request is all or nothing. A write that is refused
gets 403 (not allowed), 400 (doesn't fit) or 409 (the PLC refused it or isn't
connected), and is never retried, so a setpoint can't arrive late.

### Give the PLC a route to this machine

On Linux and macOS, pyads talks to the PLC directly, and the
PLC needs a static route back to this machine: its IP address, and an AMS net
id of that address followed by `.1.1` (sightglass prints both if the PLC
doesn't answer). Add it in TwinCAT, or with pyads' `add_route_to_plc`. On
Windows, pyads goes through TwinCAT's own ADS router: install TwinCAT (XAE,
XAR or the TC1000 ADS setup) and add the route there.

No PLC to hand? `sightglass --demo load --open` reads a fake one over real ADS
([the load demo](performance.md)).
