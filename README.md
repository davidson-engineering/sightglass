# Sightglass

Live dashboards for a running program, a microcontroller, a PLC, or anything
that can make an HTTP request. Send values by name and they appear in the
browser (or the terminal) as they change: a window onto a running system, like
the sight glass on a tank.

![Launch control: a rocket launch shown live, with a countdown clock, gauges, engine lamps, propellant tanks, a go/no-go poll, weather and an event log](https://raw.githubusercontent.com/davidson-engineering/sightglass/main/docs/launch-control.png)

That's the launch control demo, a plain HTML page fed live by three separate
programs. Run it with one command:

```bash
uvx --from "sightglass[web]" sightglass --demo launch --open
```

- **One line to start.** `start()` serves a dashboard and prints its address;
  `monitor.set("progress", 5)` works from any thread.
- **No code needed.** Pipe a program into `sightglass`, `curl` values to it,
  or point it at a serial port, ZeroMQ, a Beckhoff PLC, a REST API or InfluxDB.
- **Plain HTML pages.** Mark an element `data-bind="<id>"` and it stays live;
  CSS turns values into gauges and lamps. Or let sightglass draw a generic page.
- **Honest about staleness.** Pages reconnect by themselves, dim values that
  stopped arriving, and leave a gap rather than a stale value.
- **See what arrives.** Press `` ` `` on any page for the signals view: every
  id received, as sent, with a chart of its last minute.

## Install

Python 3.11 or newer; tested on 3.11 to 3.15.

```bash
pip install "sightglass[web]"   # add serial, zmq, ads, rest, influxdb, or use [all]
```

`sightglass --demo --open` shows every kind of display on the generic page.

## Send your first values

### From inside a Python program

```python
import time

from sightglass import start

monitor = start()  # prints "Dashboard: http://127.0.0.1:8080/"

for i in range(1, 101):
    time.sleep(0.1)  # your work
    monitor.set("progress", i)
    monitor.set("status", "running" if i < 100 else "done")
```

`set()` returns at once and is safe from any thread. Each new id becomes an
element: a lamp for `True`/`False`, text otherwise. Dotted ids like
`"job.rate"` are grouped under `job`. The dashboard stops when your program
exits. To show the program's own CPU, memory and log as well, see
[Monitor your own Python app](#monitor-your-own-python-app).

### Without writing code

```bash
# Pipe a program: lines like "progress=5 status=running" or JSON objects
# become values; every other line is printed as usual.
my_program | sightglass

# Push values from anything: shell scripts, cron jobs, other languages.
sightglass &
curl -d 'temperature=21.5 status=ok' http://127.0.0.1:8080/update
curl -d '{"job": {"progress": 5}}' http://127.0.0.1:8080/update
curl http://127.0.0.1:8080/values     # what the dashboard shows, as JSON
```

`sightglass --help` lists every option, including `--terminal` to draw in the
terminal instead of the browser.

### From a separate Python program

`Client` uses only the standard library, so the sending program doesn't need
the dashboard's dependencies:

```python
from sightglass import Client

dashboard = Client()  # http://127.0.0.1:8080
dashboard.set("progress", 5)
dashboard.update({"rate": 18.6, "status": "running"})
```

Updates are sent in order in the background, retried until the dashboard is
up, and flushed when your program exits. Pass `token=` if the dashboard has
one.

## Monitor your own Python app

Its resource use, its counters and its log, on one page:

```python
import logging
import time

from sightglass import LogHandler, ProcessSource, start

monitor = start(sources=[ProcessSource()])  # CPU, memory, threads, once a second
logging.basicConfig(level=logging.INFO)
logging.getLogger().addHandler(LogHandler(monitor))  # the last 20 records, as "log"
log = logging.getLogger("jobs")

done = failed = 0
for job in range(1, 51):
    try:
        time.sleep(0.1)  # your work
        if job % 9 == 0:
            raise ValueError("bad input")
        done += 1
    except ValueError as error:
        failed += 1
        log.warning("job %d failed: %s", job, error)
    monitor.update({"jobs.done": done, "jobs.failed": failed, "jobs.left": 50 - job})
log.info("all jobs run: %d done, %d failed", done, failed)
```

- `ProcessSource` sends `process.cpu`, `process.rss` (shown in MB),
  `process.threads`, `process.fds` (open files, not on Windows) and
  `process.uptime`. It needs nothing installed, and uses psutil if it is.
- `LogHandler` puts every record that reaches the logger in a `LogMonitor`,
  warnings and errors in colour. Records sightglass logs itself, such as a
  rejected value, arrive there too.
- `update()` sets several ids in one call. It is the cheaper way to send
  several at once: one call of 100 ids cost 0.45 µs a value on a laptop,
  against 3.7 µs for each `set()`.

## Connect a device, PLC or API

| To read | Run |
| --- | --- |
| a serial device printing CSV | `sightglass --serial auto --csv temperature,humidity` (or `--json`, `--binary`) |
| a ZeroMQ publisher | `sightglass --zmq tcp://localhost:5556` (`--pull` for PUSH senders: nothing is lost) |
| a Beckhoff TwinCAT PLC over ADS | `sightglass --ads 5.12.34.56.1.1 --vars 'MAIN.*'`, or an interface file: `sightglass --ads plc.toml` |
| a JSON REST API | `sightglass --rest http://localhost:8000/metrics --rest-interval 2` |
| several sources, or InfluxDB | a [config file](#describe-a-dashboard-in-a-file) |

Each needs its extra: `serial`, `zmq`, `ads` or `rest`.
[Sources](https://github.com/davidson-engineering/sightglass/blob/main/docs/sources.md)
covers each one, its Python class, the wire formats, and writing to a PLC.

## Choose how values look

Declare elements for bars, charts, units and number formats. Anything you
don't declare still appears, as text.

```python
from sightglass import Monitor, ProgressBar, Sparkline, TextFormat

monitor = Monitor()
monitor.add(
    ProgressBar("progress", total=500, label="Progress"),
    Sparkline("rate", label="Items/s", format=TextFormat(precision=1)),
)
monitor.start()
```

| Element | Shows | Update with |
| --- | --- | --- |
| `TextElement` | a value, optionally scaled and formatted | any value |
| `Sparkline` | a number and a chart of its recent values | a number |
| `ProgressBar` | a bar filling from 0 to `total` | a number |
| `RangeBar` | a marker on a track from `min` to `max` | a number |
| `IndicatorLamp` | on/off | `True`/`False`, `1`/`0`, `on`/`off` |
| `MachineState` | named on/off states packed into one integer | an integer (bit 0 = first state) |
| `Counters` | a row of labelled counts | a mapping, or per field: `"<id>.done"` |
| `Coordinate` | a multi-axis position | a list, or per axis: `"<id>.x"` |
| `Table` | a grid of cells | per cell: `"<id>.<row>.<column>"` |
| `LogMonitor` | the latest messages, warnings and errors in colour | a string; `LogHandler` sends log records |

Elements that show a number take `format=TextFormat(precision=1)`; lamps take
`on_style=` and `off_style=`. `Monitor(strict=True)` rejects ids you didn't
declare, and `max_elements` (2000 by default) stops a runaway feed creating
elements without end.

A monitor runs in the background (`monitor.start()`), in the foreground until
Ctrl+C (`monitor.serve()`), or inside your asyncio program
(`await monitor.run(...)`).

## Pick a page

| | Generic page | Config file | Your own page |
| --- | --- | --- | --- |
| What you write | nothing | a TOML file of panels | HTML and CSS |
| Layout | one column, grouped by dotted id | sections of panels | anything |
| Values over time | a `Sparkline` you declare (the last 60 values) | `trend` panels: plots over minutes | `data-plot`: plots over minutes |
| Fed from your own program | `start()` | [`load("dashboard.toml").start()`](https://github.com/davidson-engineering/sightglass/blob/main/docs/config.md#feed-it-from-your-own-program) | `start(outputs=[WebDashboard("page.html")])` |
| Fed by devices and APIs | `sources=[...]` | `[[source]]` tables | `sources=[...]` |

Every page also takes `POST /update` from curl and `Client`, and has the
signals view (`` ` ``).

## Build your own page

Write plain HTML and bind elements by id:

```html
<link rel="stylesheet" href="/_sightglass/instruments.css">
<span data-bind="temperature"></span>
<div class="led" data-bind="pump" data-mode="state"></div>
<div class="needle" data-bind="pressure.ratio" data-mode="var"></div>
<div data-plot="wave.*" data-seconds="60" style="height: 300px"></div>
<script src="/_sightglass/sightglass.js"></script>
```

Then serve it:

```python
from sightglass import WebDashboard, start

monitor = start(outputs=[WebDashboard("page.html")], history=600)
```

`history=600` keeps 10 minutes for `data-plot`. History keeps values as
sent, so an element's `scale` and `units` don't reach a plot: give the plot
its own, such as `data-plot-units="MB"` and
`data-plot-scale="0.00000095367431640625"` for bytes.

[Pages](https://github.com/davidson-engineering/sightglass/blob/main/docs/pages.md)
covers every binding, the signals view, plots against time (`--history`), the
terminal theme, and drawing your own layout in the terminal.

## Describe a dashboard in a file

`sightglass --config dashboard.toml` builds a whole dashboard from a file:
its sources and the panels that show them, with no page to write.

```toml
title = "Plant"

[[source]]
type = "rest"
url = "http://localhost:8000/api/readings"
prefix = "plant"

[[panel]]
type = "gauge"
title = "Pressure"
id = "plant.pressure"
units = "bar"
max = 10

[[panel]]
title = "Temperatures"
ids = ["plant.*.temperature"]   # plotted against time
```

[Dashboards from a config file](https://github.com/davidson-engineering/sightglass/blob/main/docs/config.md)
lists every source, panel and setting.

## Open it to other machines safely

The dashboard listens on `127.0.0.1` by default, so only the same machine can
see it. With `--host 0.0.0.0` (`host="0.0.0.0"`), anyone who can reach the
port can view the values and, unless you set `--token`, post updates. Viewing
has no authentication and traffic is not encrypted: on untrusted networks,
reach the dashboard through an SSH tunnel or a reverse proxy with TLS and
authentication.

Values for a device (a PLC variable marked `write = true`) are only accepted
with the token. The token crosses the network as plain text unless the
dashboard is behind TLS, so a dashboard that writes to a PLC from other
machines needs the tunnel or proxy too.

## Examples

[examples/](https://github.com/davidson-engineering/sightglass/blob/main/examples/README.md)
is a gallery where each example shows one way to feed a dashboard, and runs
with one command and nothing to install beyond
[uv](https://docs.astral.sh/uv/): a Python program, the shell, a pipe, many
processes, Docker, the terminal, a serial device on a custom page, a PLC, and
three config-file dashboards.

## More documentation

- [Sources](https://github.com/davidson-engineering/sightglass/blob/main/docs/sources.md):
  serial, ZeroMQ, PLCs over ADS, REST, InfluxDB, and the wire formats.
- [Dashboards from a config file](https://github.com/davidson-engineering/sightglass/blob/main/docs/config.md):
  every source, panel and setting.
- [Pages](https://github.com/davidson-engineering/sightglass/blob/main/docs/pages.md):
  bindings, the signals view, plots, themes and the terminal.
- [How fast it is](https://github.com/davidson-engineering/sightglass/blob/main/docs/performance.md):
  the load demo, and what 1000 to 5000 signals cost.
- [Extending sightglass](https://github.com/davidson-engineering/sightglass/blob/main/docs/extending.md):
  your own elements, decoders, sources and outputs.
- [CONTRIBUTING.md](https://github.com/davidson-engineering/sightglass/blob/main/CONTRIBUTING.md):
  development and releases.

## For AI coding agents

`sightglass --guide` prints a concise, recipe-first guide to building monitors,
written for coding agents. To make it discoverable in another project, add a
line like this to that project's `CLAUDE.md` or `AGENTS.md`:

```markdown
- Live dashboards for this project: `sightglass`; run `sightglass --guide` before building one.
```

## License

MIT. The bundled fonts are under the SIL Open Font License 1.1: DSEG
(© keshikan), B612 (© The B612 Project Authors), VT323 (© The VT323 Project
Authors), Micro 5 (© The Soft Type Project Authors) and Silkscreen (© The
Silkscreen Project Authors). Their licence files are beside them, in
`src/sightglass/web/static/fonts/` and `src/sightglass/launch/static/fonts/`.
