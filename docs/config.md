# Dashboards from a config file

`sightglass --config dashboard.toml` builds a whole dashboard from a file: the
sources, and the panels that show them. There is no page to write, and the file
can be kept apart from sightglass, one per dashboard.

```toml
title = "Plant"

[[source]]
type = "rest"                                  # poll a JSON API with GET
url = "${API_URL:-http://localhost:8000/api/readings}"
prefix = "plant"                               # ids start "plant."
select = "sensors"                             # start from this place in the JSON
exclude = ["plant.*.firmware"]                 # id patterns to leave out (or include = [...])
rate = ["plant.*.count"]                       # counters shown as a change per second
interval = 0.5                                 # seconds between polls

[[panel]]                                      # a trend, a panel per name
each = ["North", "South"]
title = "{name}"
ids = ["plant.{name}.value.*"]                 # * stands for any characters
range = 60                                     # the primary plot: the last minute
secondary_range = 300                          # the secondary, above it: 5 minutes
```

Not sure what ids a source makes? `sightglass --rest URL --list` polls once,
prints each id with its value and exits (`--config dashboard.toml --list` does
the same for a file's sources), so the panels' `ids` can be copied from it.

A web service's metrics, health check and event feed make three `rest`
sources and a few panels: [Monitoring a web service](https://github.com/davidson-engineering/sightglass/blob/main/src/sightglass/GUIDE.md#monitoring-a-web-service)
walks through one, and `examples/web_service/dashboard.toml` is it.

A mistake in the file, such as an unknown key, is reported in one clear line.
[examples/](https://github.com/davidson-engineering/sightglass/blob/main/examples/README.md#10-a-dashboard-from-a-config-file-a-rest-api)
has three config dashboards to copy: a REST API, a PLC and an InfluxDB
database.

## The words the config uses

The code, the config and these docs all use these words:

| Term | Meaning |
| --- | --- |
| **dashboard** | one page, described by one config file; run one `sightglass --config` per dashboard |
| **source** | where values come from: a `[[source]]` of a `type` (`rest`, `ads`, `zmq`, `serial`, `influxdb`) |
| **id** | a value's name, such as `plant.North.value.0.1`; sources make them and panels show them |
| **section** | a titled group of panels: `[[section]]`, with its panels as `[[section.panel]]` |
| **panel** | one tile of the page: a `[[panel]]` of a `type` (`trend`, `value`, `lamp`, `gauge`, `log`) |
| **plot** | what a `trend` panel draws, from the dashboard's history: a **primary** plot and, optionally, a **secondary** one |
| **range** | the seconds of history a plot covers |
| **element** | the monitor's own display model (`TextElement`, `RangeBar`, ...) that hand-written pages bind; a panel is not one, but a `value` panel with `precision` declares one |

## How the page is laid out

The page is a dark, large-text layout for reading from across a room. It is
one screen with nothing to scroll.

- **Sections** stack from top to bottom, each with its heading if it has a
  `title`. Panels written at the top level, with no `[[section]]`, are one
  section without a heading.
- **Height:** a section asks for height by its tallest panel, so a row of
  readings is short and a row of plots is tall.
- **Width:** panels wrap like Grafana's. Each is at least `panel_width`
  pixels wide (520 by default, and readings 60% of that), so the count per row
  follows the screen, and the last row's panels grow to fill it.

## Panels: what each tile shows

| `type` | Shows | Keys |
| --- | --- | --- |
| `trend` (the default) | `ids` against time: a primary plot and, optionally, a secondary one over another range | `ids` (id patterns, `*` for any characters), `mode` (`"lines"`, or `"heatmap"`, a row per id, for hundreds of ids), and the plots' settings below |
| `value` | one id's current value as large text | `id`, `units`, `precision` (decimals) |
| `lamp` | one id as a light: on for `true`, non-zero and `"on"`, off otherwise | `id` |
| `gauge` | one id as a bar between a minimum and a maximum, with the value above it | `id`, `min` (default 0), `max` (default 100), `units`, `precision` |
| `log` | one id's last lines of text, newest at the bottom: an event feed (a `LogMonitor`) | `id`, `lines` (default 10), `timestamp` (put the time before each line; default false) |

Every panel also takes `title`, and `each`: a list of names that makes one
panel per name, with `{name}` replaced in the title and in `id` or `ids`. A
`trend` takes patterns, because it plots many; the others take one exact
`id`. Two panels may show the same id, but not to different `precision`s,
because the monitor formats an id once.

An invalid value (`null` from a REST API, a series InfluxDB has stopped
getting points for, a source that went down) is never left showing: a plot has
a gap, a `value` is blank, a `lamp` is off and a `gauge` is empty.

```toml
[[section]]
title = "Now"

[[section.panel]]
type = "gauge"
title = "Pressure"
id = "plc.pressure"
units = "bar"
max = 10

[[section.panel]]
type = "lamp"
each = ["A", "B"]
title = "Pump {name}"
id = "plc.pump{name}.running"

[[section]]
title = "Last 5 minutes"

[[section.panel]]
ids = ["plc.pressure"]
range = 300
secondary = false      # just the one plot
```

### A trend's two plots

The primary is the plot to read; the secondary is context, drawn smaller. A
plot's colour follows its role, so the primary is yellow and the secondary
blue wherever they are placed.

| Key | Meaning | Default |
| --- | --- | --- |
| `range` | seconds of history the primary plot covers | `60` |
| `secondary` | whether there is a secondary plot (`true` or `false`) | `true` |
| `secondary_range` | seconds the secondary plot covers | `300` |
| `secondary_ratio` | the secondary plot's height over the primary's | `0.5` |
| `first` | which plot is on top, `"secondary"` or `"primary"` | `"secondary"` |

With `secondary = false`, the other three secondary settings are an error.

### A panel that shows nothing

Once every source has answered a poll, sightglass warns (once, on stderr) about
each panel whose id or pattern matches nothing, with the closest id it did
receive:

```text
panel "CPU" (section "Host"): no id 'res.cpu_percent'; did you mean 'res.host.cpu_percent'?
```

It only checks when every `[[source]]` is a polling one (rest, influxdb); a
source that can deliver an id at any time (zmq, serial, ads, `POST /update`)
turns the check off.

### What panels cost

The dashboard keeps history only for the ids its `trend` panels plot. A
dashboard of plots alone opens no live-values connection, so a source with
thousands of ids costs little more than polling it. A `value`, `lamp`,
`gauge` or `log` needs the live connection, which sends every id the source
makes to the page (an id that arrives again unchanged costs its name, about
30 bytes, and a changed one its value too), so keep those dashboards to
sources with a few hundred ids: 900 ids polled every 0.2 s send each open page
about 150 KB/s ([performance.md](performance.md)).

## Settings at the top of the file

| Key | Meaning | Default |
| --- | --- | --- |
| `title` | the page heading | `"Dashboard"` |
| `version` | a label for this dashboard's own version (a `v` goes before one that starts with a digit: `1.2` shows `v1.2`, `spike` stays `spike`), shown at the right of the title bar with the config's name and fingerprint, the sightglass version and the start time | none |
| `theme` | the look unless a viewer switches: `"classic"` or `"terminal"` | `"classic"` |
| `stale_after` | seconds without new data before values dim | never |
| `history` | seconds of values kept for plots | the longest `range`, and a minute |
| `max_elements` | the most ids shown | `2000` |
| `panel_width` | the least width of a panel, in pixels | `520` |
| `title_bar` | where the title bar goes: `"top"` or `"bottom"` | `"top"` |

History is on by default here, unlike `sightglass --history`, because trend
panels need it.

## What the command line still sets

The file says what the dashboard is; the command line says where and how it
runs. With `--config`, only `--host`, `--port`, `--token`, `--open`, `--debug`
and `-v` are taken. Any other option is refused with a message naming it.

## Feed it from your own program

A file needs no `[[source]]`. Without one, the dashboard shows the values sent
to it, as any dashboard does: by `POST /update`, curl, or `Client` from
another process.

```bash
sightglass --config dashboard.toml &
curl -d 'app.cpu=12.5' http://127.0.0.1:8080/update
```

A Python program can serve the file itself and set values directly:

```python
from sightglass.dashboard import load

monitor = load("dashboard.toml").start(port=8080)  # prints the address
monitor.set("app.cpu", 12.5)
```

`start()` takes what the command line would: `host`, `port`, `token`,
`debug` and `open_browser`. It reads the file's sources too, if it has any.
In an asyncio program, `dashboard.monitor()` and `dashboard.web(port=8080)` are
the two parts: `await monitor.run(sources=dashboard.sources, outputs=[web])`.

## What still needs Python

The file takes the five panel types and the sources below, and nothing it
doesn't name: for a source of your own (a login first, a POST, XML), a value
worked out from others, a table, progress bar or counter display, writes to a
PLC, or a page of your own layout, serve the file from Python and add the
rest (`load("dashboard.toml").start()`, then `monitor.set(...)`, as in
[Feed it from your own program](#feed-it-from-your-own-program)).

## Sources and their keys

A dashboard takes any number of sources, of any mix, and a panel can show
every id they make. [Sources](sources.md) describes each kind in full.

| `type` | Keys |
| --- | --- |
| `rest` | `url`, `prefix`, `select`, `key`, `include`, `exclude`, `rate`, `events`, `cursor`, `line`, `id`, `interval`, `timeout`, `accept`, `headers`: see [REST APIs](sources.md#rest-apis) |
| `ads` | `target` (the AMS net id) and `variables` (names, `*` for any characters, or a table of types), `types`, `ip`, `interval`, `timeout`; or `file`, an interface file beside the config, in place of those that describe the PLC. Ids are the PLC's variable names. Never writes to the PLC: `write = true` on a variable is refused here (an interface file's marks are left unused) |
| `zmq` | `endpoint`, `pattern` (`"sub"` or `"pull"`), `bind`, and a `decoder` |
| `serial` | `port` (default `"auto"`), `baudrate`, and a `decoder` |
| `influxdb` | InfluxDB 2.x: `url`, `org`, `token`, and a `bucket` (narrowed by `measurement`, `fields`, `tags`) or a Flux `query`; `window`, `id_from`, `prefix`, `interval`, `timeout`: see [below](#influxdb) |

A `zmq` or `serial` source's `decoder` is `"keyvalue"` (the default), `"json"`,
`"csv"` with `fields = ["a", "b"]`, or `"binary"` with `names = { "1" = "X" }`.

`timeout` defaults to what each protocol needs: 2 s for a PLC (it answers in
milliseconds, so longer means the link is down), 5 s for a REST API (a web
service can be slow), 10 s for InfluxDB (a query over many series takes time).

### InfluxDB

An `influxdb` source runs its query every `interval` seconds and shows the
newest point of each series, with the id made by joining the columns named in
`id_from` (default `["_measurement", "_field"]`, so `cell.voltage`). A series
with no point in the last `window` seconds (default 60) is blanked, as is
everything while the server can't be reached, so a gap in the data is a gap in
the plot and never a stale value. With `query`, the Flux must end with one row
per series (`|> last()`); its own `range()` is the window.

```toml
[[source]]
type = "influxdb"
url = "http://localhost:8086"
org = "plant"
token = "${INFLUX_TOKEN}"                      # the token stays out of the file
bucket = "readings"
measurement = "cell"
id_from = ["_measurement", "unit", "_field"]   # cell.A.voltage
```

## Keep secrets out of the file

Text in a source may hold `${NAME}` or `${NAME:-default}`, replaced by that
environment variable, so a token never has to be in the file. Write `$${` for
a literal `${` (Flux has its own, in an InfluxDB `query`).
