# Building monitors with sightglass: a guide for AI agents

`sightglass` shows live values from a program, a device, or anything that
can make an HTTP request, in a browser (default) or the terminal. Values are
sent by id (`"progress"`, `"X.velocity"`); the first value sent for an id
creates its display. Print this guide from any project with
`sightglass --guide`; full reference: `sightglass --help`, the README and
https://github.com/davidson-engineering/sightglass/blob/main/docs/.

## 1. Install

```bash
pip install "sightglass[web]"     # add serial / zmq / ads / rest / influxdb extras as needed, or [all]
```

Python 3.11+. The package has no required dependencies; `[web]` adds
aiohttp for the browser dashboard, `[serial]` pyserial, `[zmq]` pyzmq,
`[ads]` pyads (Beckhoff PLCs).

## 2. Pick the integration

| Situation | Do this |
| --- | --- |
| You can edit the Python program | `monitor = start()`, then `monitor.set(id, value)` anywhere |
| Another language, a shell script, or a program you can't edit | run `sightglass`, then `POST /update` (curl) |
| The program can print lines | print `key=value` pairs; run `program \| sightglass` |
| A separate Python process should report to a shared dashboard | `Client().set(id, value)` (standard library only) |
| A microcontroller on a serial port | `sightglass --serial auto --csv a,b,c` (or `--json`, `--binary`) |
| A ZeroMQ publisher | `sightglass --zmq ENDPOINT` (`--pull` for PUSH senders: nothing lost; `--json`, `--csv`, `--binary` as for serial) |
| A Beckhoff TwinCAT PLC (ADS) | `sightglass --ads NETID --vars 'MAIN.*'`, or an interface file: `sightglass --ads plc.toml` |
| A REST API that answers GET with JSON | `sightglass --rest http://host:8000/metrics` (repeat `--rest`; `--rest-interval`, `--rest-header 'X-Api-Key: ...'`), or `RestSource("http://host/metrics", interval=2)`; needs `[rest]` |
| A dashboard that is a file, one per instance, kept apart from the code | `sightglass --config dashboard.toml`: `[[source]]` (rest, ads, zmq, serial or influxdb; or none, fed by `POST /update`) and `[[panel]]` tables (trend, value, lamp, gauge; `[[section]]` to group them), no page to write; from Python, `load("dashboard.toml").start(port=8080)` (`from sightglass.dashboard import load`) serves it and returns the monitor; every key: https://github.com/davidson-engineering/sightglass/blob/main/docs/config.md |
| An asyncio program | `await monitor.run(outputs=[WebDashboard()])` in a task |
| A web service: metrics, health check, event feed | `RestSource` with `Endpoint`s (`rate`, `accept`, `events`), or a `--config` file: [Monitoring a web service](#monitoring-a-web-service) |
| Just show the user what it looks like | `sightglass --demo --open` (every display), `sightglass --demo launch --open` (a hand-built page), `sightglass --demo plot --open` (value-vs-time plots), `sightglass --demo load --open` (a fake PLC's 1000 signals over ADS, and what they cost; needs the ads extra) |

Choose `start()` when the dashboard should live and die with the program.
Choose a separate `sightglass` process (fed by `Client`, curl or a pipe) when
several programs report to one dashboard, or it should outlive them.

## 3. Recipes

### Inside a Python program

```python
import time

from sightglass import start

monitor = start()  # prints "Dashboard: http://127.0.0.1:8080/", returns at once
monitor.set("status", "loading")
for i in range(1, 51):
    time.sleep(0.1)  # the program's real work
    monitor.set("progress", i)
    monitor.set("rate", i / (0.1 * i))
monitor.set("status", "done")
```

- `set()` never blocks and is safe from any thread. To send several ids at
  once, `monitor.update({"a": 1, "b": 2})` is one call (about 0.45 µs a value
  for 100 ids, against 3.7 µs for each `set()`).
- `start()` raises `OSError` if the port is taken; pass
  `outputs=[WebDashboard(port=0)]` for any free port (the address is printed,
  and is `web.url` on the `WebDashboard` object).
- When the program exits, open pages keep the final values, marked
  disconnected.

### Monitoring a Python app itself

Its resource use, its counters and its log, on one page:

```python
import logging

from sightglass import LogHandler, ProcessSource, start

monitor = start(sources=[ProcessSource()])  # process.cpu, .rss, .threads, ...
logging.basicConfig(level=logging.INFO)
logging.getLogger().addHandler(LogHandler(monitor))  # the last 20 records, as "log"
log = logging.getLogger("jobs")

monitor.update({"jobs.done": 41, "jobs.failed": 2, "jobs.left": 7})
log.warning("job 42 failed")  # warnings amber, errors red
```

- `ProcessSource()` samples once a second: CPU (% of one core), memory
  (`process.rss`, bytes, shown in MB), Python threads, open files, uptime and
  garbage collector counts. No other code; psutil is used if installed, not
  needed.
- `LogHandler` also takes a `Client`, and an id and level:
  `LogHandler(monitor, "events", level=logging.WARNING)`.
- For plots over minutes rather than sparklines, use a config file of
  `trend` panels or a page with `data-plot` (section 5); start with
  `history=600`.

| Page | What you write | Values over time | From your own program |
| --- | --- | --- | --- |
| generic | nothing | declared `Sparkline`s | `start()` |
| config file | TOML panels | `trend` panels | `sightglass.dashboard.load("dashboard.toml").start()` |
| your own | HTML, CSS | `data-plot` | `start(outputs=[WebDashboard("page.html")])` |

### Monitoring a web service

Metrics, a health check and an event feed are three different things, and each
is read its own way. This is one dashboard of all three; `examples/web_service/`
runs it against a fake service.

```python
from sightglass import Endpoint, RestSource, start

service = "http://localhost:8000"
monitor = start(
    sources=[
        RestSource(
            Endpoint(  # metrics: numbers, and counters that only grow
                f"{service}/metrics",
                prefix="svc",
                rate=["svc.service.requests_total"],
            ),
            Endpoint(  # a health check: a 503 with a body is still data
                f"{service}/health", prefix="health", accept=(503,)
            ),
            Endpoint(  # events: one line of a log each, no id per event
                f"{service}/events",
                events="events",
                cursor="id",
                line="{time} {level}: {message}",
                id="events",
            ),
            interval=1,
        )
    ]
)
```

- **Metrics:** a counter (`requests_total`) is useless as a growing number;
  list it in `rate` and it shows the change per second. A list of numbers
  gives `load.0`, `load.1`; a list of objects with an `id` field gives
  `workers.worker-1.rss_mb` (the `id` field names the item and is not an id
  itself). For a percentage of use (CPU, a pool), `RangeBar(id, min=0,
  max=100, units="%")` draws a marker on a track and still shows a reading
  past the ends; `ProgressBar` fills like a level but is named for progress
  towards `total`, and a config file's `gauge` panel is the same bar.
- **Health:** a service that is down often answers 503 with a body saying
  which check failed. Without `accept=(503,)` that reply is ignored, and the
  page shows a gap, not the reason.
- **Lists that change** (events, jobs, alerts) make new ids each poll, which
  stay on the page and count against `max_elements`. For things that happened,
  use the events mode (`events`, `cursor`, `line`, `id`: each new event is
  one line of a `LogMonitor`); for things that exist now, `select` the object
  of counts that sums them up (`select="jobs.by_state"`) rather than the list.
- **Finding the ids first:** `sightglass --rest URL --list` polls once and
  prints every id with its value, before you write panels or elements.
- **As a file:** the same three sources and their panels (`lamp`, `gauge`,
  `trend`, `log`) are a `--config` file with no code:
  `examples/web_service/dashboard.toml`. Python is needed only for what the
  file can't say: a source of your own, computed values, a custom page.
- Rates, event feeds, `accept` and ids for lists, in full:
  https://github.com/davidson-engineering/sightglass/blob/main/docs/sources.md#rest-apis

### Choosing how values look

Declare elements for bars, charts, lamps, units and number formats. Anything
not declared still appears, as text.

```python
from sightglass import (
    IndicatorLamp,
    Monitor,
    ProgressBar,
    RangeBar,
    Sparkline,
    TextFormat,
    WebDashboard,
)

monitor = Monitor()
monitor.add(
    ProgressBar("progress", total=50, label="Progress"),
    Sparkline("rate", label="Items/s", format=TextFormat(precision=1)),
    IndicatorLamp("healthy", label="Healthy"),
)
monitor.add_group("X", [RangeBar("velocity", min=-10, max=10, units="mm/s")])
monitor.start(outputs=[WebDashboard(title="Batch job")])
monitor.set("healthy", True)
monitor.set("X.velocity", 2.5)
```

### From any language

```bash
sightglass --title "Build farm" &
curl -d 'progress=5 status=running' http://127.0.0.1:8080/update
curl -d '{"job": {"progress": 5, "ok": true}}' http://127.0.0.1:8080/update
curl http://127.0.0.1:8080/values      # read back what is shown, as JSON
```

The body is a JSON object (or a list of objects), or lines of `key=value`
pairs / `id value`. Nested JSON objects address groups: `{"job": {"progress":
5}}` sets `job.progress`. `204` means accepted; `400` says what was wrong;
`401` means the dashboard has a `--token` and the request didn't send
`Authorization: Bearer <token>`.

### Keeping history for value-vs-time plots

```bash
sightglass --history 600 &      # or Monitor(history=600): the last 10 minutes
curl 'http://127.0.0.1:8080/history?id=speed&id=X.velocity&seconds=60&points=600'
```

Every id that sends a number is recorded in 50 ms slots (8 bytes per slot per
id: 96 MB for 1000 ids over 10 minutes). The answer has `start` (a Unix time),
`step` (seconds per point), `points`, and `signals`, a list of points for each
id. Each point is a number, `[lowest, highest]`, or `null` for no number.
Use `from`/`to` (Unix times) in place of `seconds` for a fixed window, and
set `points` to the plot's width in pixels. `id=wave.*` names every id that
starts `wave.`. To draw them on a page, see `data-plot` in section 5.

### Piping a program's output

```bash
my_program | sightglass
```

A line made entirely of `key=value` pairs (`progress=5 status="two words"`) or
a JSON object becomes values; every other line is printed through unchanged.
In Python: `print(f"progress={i} rate={rate:.1f}", flush=True)`.

### From a separate Python process

```python
from sightglass import Client

dashboard = Client("http://127.0.0.1:8080")  # token="..." if the dashboard has one
dashboard.set("progress", 5)
dashboard.update({"status": "running", "errors": 0})
```

Updates are sent in order in the background, retried until the dashboard is
reachable, and flushed when the program exits. `from sightglass.client import
send; send({"status": "done"})` sends one message immediately and raises
`ConnectionError` if it can't.

### A serial device

```bash
sightglass --serial auto --csv temperature,humidity   # device prints "21.5,48\n"
sightglass --serial /dev/ttyUSB0 --json               # {"temperature": 21.5}
sightglass --serial auto --binary 1=X,2=Y,10=velocity # binary frames: docs/sources.md
```

`auto` picks the first USB serial device. The source waits for the device and
reconnects after it is unplugged. In Python:

```python
from sightglass import CsvDecoder, Monitor, SerialSource

monitor = Monitor()
device = SerialSource(
    "auto", baudrate=115200, decoder=CsvDecoder(["temperature", "humidity"])
)
monitor.serve(sources=[device])  # runs until Ctrl+C
```

### A Beckhoff TwinCAT PLC (ADS)

```bash
sightglass --ads 5.12.34.56.1.1 --vars 'MAIN.*,GVL.fTemperature'   # :851 by default
sightglass --ads plc.toml                                          # an interface file
```

Ids are the PLC's variable names; structs and arrays give one id per member
(`MAIN.stAxis.fPosition`, `GVL.aTemps[1]`). `*` in a name matches top-level
variables only; name struct members exactly, or declare the struct. An
interface file names the PLC, the variables and their types:

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
"MAIN.stAxis1" = "ST_Axis"
"GVL.fTemperature" = {}
"GVL.nSetpoint" = { write = true }
```

`types` is Structured Text pasted from the PLC project (structs, enums,
aliases, `pack_mode` attributes). In Python: `AdsSource("5.12.34.56.1.1",
["MAIN.*"])` or `AdsSource.from_file("plc.toml")`, passed in `sources=[...]`.

- **Writes are off unless all four hold:** `write = true` on the variable,
  `--allow-writes` (`allow_writes=True`), `SIGHTGLASS_ALLOW_WRITES=1` in the
  environment, and `--token` on the dashboard (writes over HTTP need it). Then
  `curl -H 'Authorization: Bearer TOKEN' -d 'GVL.nSetpoint=75' .../update`
  writes; 204 means the PLC took it, 403/400/409 say why not. Never enable
  writes unless the user asked for them.
- **Linux and macOS:** the PLC needs a route to this machine. If it doesn't
  answer, the warning names the AMS net id and IP address to add.
- **Windows:** needs TwinCAT's ADS router installed (XAE, XAR or TC1000).
- **No PLC to hand:** `sightglass --demo load` reads a fake one over real
  ADS (`sightglass.fake_plc`, on 127.0.0.1:48898, target `127.0.0.1.1.1`).
- **More than 2000 variables:** add `--max-elements 5000` (or
  `Monitor(max_elements=5000)`); past the limit, values are dropped.
- Warnings name every variable that couldn't be read and why (unknown name,
  undeclared struct type, size mismatch with the declaration); read them.

## 4. Ids and values

- An id is any string. Dots group: `job.rate` is shown as `rate` under a `job`
  heading. Ids are case-sensitive.
- A new id creates an element: a lamp for `True`/`False`, text otherwise.
  Declare the element first to get a bar, chart, units or format.
- curl, serial and pipes send text, so `pump=on` shows the text `on`. Declare
  `IndicatorLamp("pump")` to show a lamp; it accepts `on`/`off`, `1`/`0`,
  `true`/`false`.
- Multi-part elements take fields: `table.row.column`, `machine.estop`,
  `position.x`.
- Floats that need more than 6 significant digits are shortened; use
  `TextFormat(precision=2)` for a fixed number of decimals.
- `Monitor(strict=True)` rejects ids that weren't declared (logged once per
  id); by default at most `max_elements=2000` are created (`--max-elements`
  on the command line). Ids past that are dropped, with a warning.

| To show | Element | Update with |
| --- | --- | --- |
| a value or message | `TextElement(id, units="°C", format=TextFormat(precision=1))` | anything |
| a small line of recent values | `Sparkline(id, points=60, scale=1 / 1024, units="KB/s")` | numbers |
| a longer line of a fast value | `Sparkline(id, points=300, interval=1)`: one point a second | numbers |
| progress towards a total | `ProgressBar(id, total=100)` | a number |
| a reading within limits, a utilisation percentage | `RangeBar(id, min=0, max=100, units="%")`: a marker on a track | a number |
| on/off | `IndicatorLamp(id)` | bool, 1/0, on/off |
| many flags in one integer | `MachineState(id, states=[...])` (bit 0 first) | an integer |
| a position | `Coordinate(id, axes=("x", "y", "z"))` | a list, or `id.x` |
| a row of labelled counts | `Counters(id, fields=["done", "failed"])` | a mapping, or `id.done` |
| a grid | `Table(id, rows=[...], columns=[...])` | `id.row.column` |
| recent events | `LogMonitor(id, lines=10, timestamp=True)` | a string; a line starting `WARNING` or `ERROR` (after the time) is coloured |

## 5. A custom page

For a branded or hardware-style page, write plain HTML and bind elements by
id; no JavaScript is needed. `sightglass --demo launch` runs a complete
example (gauges, tanks, lamps, several feeds) whose source ships with the
package: `python -c "import sightglass.launch as m; print(m.HERE)"` prints
where. The repository's `examples/robot_dashboard/` is another (segment
displays, LEDs), and its `examples/README.md` has a runnable example of each
route in this guide (one command each), plus a Docker deployment.

```html
<link rel="stylesheet" href="/_sightglass/instruments.css">
<span data-bind="temperature"></span>
<div class="led" data-bind="pump" data-mode="state"></div>
<div data-bind="rate.values" data-mode="sparkline" style="height: 24px"></div>
<div class="needle" data-bind="pressure.ratio" data-mode="var"></div>
<span data-bind="wind" data-stale-after="3"></span>
<div data-plot="wave.* level" data-seconds="60" style="height: 400px"></div>
<script src="/_sightglass/sightglass.js"></script>
```

Serve it with `WebDashboard("page.html", static_dir="static")` (files in
`static_dir` are at `/static/`). Bars bind `id.text` and `id.ratio`
(`data-mode="width"`); sparklines `id.text` and `id.values`. A
`data-mode="state"` element is off for `false`, `0`, `"off"`, `"0"`,
`"false"`, `"no"` and empty, and on for anything else.

- **Gauges without JavaScript:** `data-mode="var"` sets the CSS variable
  `--value` to the number (unset if it isn't one), e.g.
  `.needle { rotate: calc(var(--value) * 270deg - 135deg) }` for a `RangeBar`
  ratio, or `height: calc(var(--value) * 100%)` for a level.
- **Plots against time:** `data-plot` names ids or patterns (space-separated,
  `*` for any characters) and draws them over the last `data-seconds`, live,
  in the element's CSS `color` and `font`; two to eight ids get a colour
  each and a legend. History keeps values as sent: `data-plot-units="MB"`
  and `data-plot-scale="0.00000095367431640625"` show bytes as MB. Plot
  values of different units in separate plots: a plot has one value axis.
  It needs the dashboard's history (`--history`); without it the element
  gets `data-plot-error`.
  `data-plot-mode="heatmap"` draws a row per id instead, marking where each
  goes far from its usual value: for hundreds or thousands of ids.
- **Several feeds:** `data-stale-after="3"` gives an element
  `data-stale="true"` while its value hasn't arrived for 3 s (or ever), so
  CSS can show which source went quiet, e.g.
  `section:has([data-stale]) { opacity: 0.3 }`. Bind it to a value the source
  sends every time; resending the same value counts as arriving.
- **Themes:** pages come in `classic` and `terminal` (phosphor green,
  cockpit-style instruments, optional CRT effects); `--theme` /
  `WebDashboard(theme=...)` sets the default and a switch on the page lets
  viewers change it. For your own page:
  `<script src="/_sightglass/theme.js"></script>` and
  `<link rel="stylesheet" href="/_sightglass/terminal.css">` in `<head>`,
  buttons with `data-theme-switch` / `data-crt-switch`, and CSS under
  `html[data-theme="terminal"]` using the `--crt-*` colours and fonts
  (`--crt-display-font` for a big clock-like display, at multiples of 11px;
  `--crt-readout-font` for readings, at multiples of 8px). The CRT effects
  cover any such page; animate your boxes with its `crt-draw` and
  `crt-type` keyframes, only under `prefers-reduced-motion: no-preference`.
- **Checking the ids:** press `` ` `` on the page for the signals view: every
  id the dashboard receives, its value as sent and its last minute. A
  `data-bind` that stays empty names an id that isn't in that list.
- **In a terminal:** `TerminalDisplay(screen=fn)` draws your own layout:
  `fn(monitor, width, height)` returns the whole frame, reading
  `monitor[id].text` and coloured with `Style(fg=214)` (xterm's 256
  colours). `sightglass --demo launch --terminal` runs a full one, in the
  package's `sightglass/launch/screen.py`.

## 6. Deploying

- **Other machines:** `WebDashboard(host="0.0.0.0")` / `--host 0.0.0.0`.
  Anyone who can reach the port can then view the values, and post values
  unless `token` / `--token` is set. Viewing is never authenticated or
  encrypted: on untrusted networks use `ssh -L 8080:127.0.0.1:8080 host` or a
  reverse proxy with TLS and auth.
- **Continuous sources:** `stale_after=2` / `--stale-after 2` dims values
  when no data has arrived for 2 s.
- **Ports:** 8080 by default. On macOS avoid 5000 (AirPlay).
- **As a Linux service** (a dashboard that outlives the programs feeding it):

  ```ini
  # /etc/systemd/system/sightglass.service
  [Unit]
  Description=Live dashboard
  After=network-online.target

  [Service]
  ExecStart=/opt/sightglass/venv/bin/sightglass --host 0.0.0.0 --token change-me
  Restart=on-failure

  [Install]
  WantedBy=multi-user.target
  ```

## 7. Verify before saying it works

1. The program printed `Dashboard: http://...`; use that address.
2. `curl -s http://127.0.0.1:8080/values` lists the ids with their current
   values (what the page shows).
3. For pushed values, `curl -s -o /dev/null -w '%{http_code}' -d 'check=1'
   http://127.0.0.1:8080/update` prints `204`.
4. Warnings (bad values, unknown ids with `strict=True`, a missing serial
   device, PLC variables left out) go to stderr; read them.
5. Look at the page in a browser if you can: values change live and the
   status says "Live". Press `` ` `` for the signals view: every id received,
   as sent, with a chart of its last minute.

## 8. Gotchas

- A program that exits takes a `start()` dashboard with it. For a dashboard
  that stays up, run `sightglass` separately and send with `Client` or curl.
- `TerminalDisplay` / `--terminal` redraws the whole terminal: don't print
  while it runs (the command logs to `sightglass.log` instead).
- ZeroMQ PUB/SUB drops messages sent before the subscriber connects; use
  PUSH with `ZmqSource(endpoint, pattern="pull")` / `--zmq ... --pull`.
- In asyncio code use `await monitor.run(...)`; `start()` runs its own loop in
  a thread and `serve()` blocks.
- In tests: `WebDashboard(port=0, announce=False)`, `monitor.start(...)`,
  `web.url`, then `monitor.stop()`. `SimulatedSource(fn, interval=0.05)` generates
  data without hardware.
