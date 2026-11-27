# CLAUDE.md

`sightglass`: live web and terminal dashboards fed by programs, devices and
HTTP. The guide below is how to *use* it to build a monitor; it ships with the
package (`sightglass --guide`), so agents in other projects get the same
instructions.

@src/sightglass/GUIDE.md

---

# Working on this repository

## Commands

```bash
uv sync --all-extras                    # venv with every extra and dev tool
uv run playwright install chromium      # once: the browser tests need it
uv run pytest                           # ~300 tests, ~30 s (-m "not browser" skips Chromium)
uv run ruff check . && uv run ruff format --check .
uv run sightglass --demo               # see the generic page with every element type
uv run sightglass --demo launch --open  # the showcase (README screenshot); python -m sightglass.launch --at 60 starts mid-flight
uv run sightglass --demo plot --open    # 100 waveforms at 20 Hz on value-vs-time plots (data-plot)
uv run sightglass --demo load --open    # a fake PLC's 1000 signals over real ADS, measured; uv run python -m sightglass.load --signals 5000 for more
uv run python examples/robot_dashboard/dashboard.py --simulate   # the segment-display page
uv run --all-extras python docs/screenshot.py   # retake the README screenshots (~3 min; `plot` for just the plot demo's)
```

## Layout

| Path | What |
| --- | --- |
| `src/sightglass/monitor.py` | `Monitor`: element registry, id routing, auto-created elements, change versions, `accepted`/`rejected` counts, thread-safe updates, `start`/`serve`/`run`/`stop` |
| `src/sightglass/elements.py` | element types; each has `update`, `to_json` (browser) and `render(width)` (terminal) |
| `src/sightglass/history.py` | `Monitor.history` (`history=seconds`, `--history`): every numeric id's recent values in 50 ms slots (float32 lowest and highest, carried forward), reduced per query to points aligned on multiples of their step; `Track`, the slot ring the signals view shares; standard library only |
| `src/sightglass/signals.py` | `Monitor.signals`: every accepted value by the id it was sent to, as sent, with its last minute in time slots shared by all ids (each slot its lowest and highest value); what the signals view shows; standard library only |
| `src/sightglass/decoders.py` | bytes to `{id: value}`: CSV, key/value + logfmt pairs, JSON, binary frames |
| `src/sightglass/sources/` | serial, ZeroMQ, ADS (Beckhoff PLCs, `ads.py`: sum reads, symbol lookup, guarded writes), stdin, simulated; `_thread.py` runs blocking I/O off the event loop |
| `src/sightglass/sources/rest.py` | `RestSource`/`Endpoint` (aiohttp client): polls JSON URLs, one task per endpoint on its own schedule, flattens to dotted ids; a failing endpoint is logged once and retried |
| `src/sightglass/sources/_poll.py` | what the polling sources (REST, InfluxDB) share: `read_body` (a whole reply, or refused over the limit, up front if it says so: never `content.read(n)`, which returns one chunk), `parse` (a reply over 256 KB is parsed in a worker thread, so the loop is held up by `json.loads` alone, which keeps the GIL for its whole call), `Feed` (polls a second and poll times, listed in the signals view through `Monitor.feeds()`), `poll` (the schedule; any failure, expected or not, is a failed poll, logged when it starts and when its cause changes; invalid ids are blanked) |
| `src/sightglass/sources/influx.py` | `InfluxSource` (aiohttp client, InfluxDB 2.x): runs a Flux query each interval, the newest point of each series becomes a value; a series with no point in `window` seconds, or an unreachable server, is blanked like a REST `null`; `rows` parses the annotated CSV |
| `src/sightglass/dashboard/` | `sightglass --config FILE`: `__init__.py` (`load`: a TOML file into a `Dashboard` of `Section`s of `Panel`s and its sources, one-line errors for mistakes), `sources.py` (`[[source]]` of a type: rest, ads, zmq, serial, influxdb; `${ENV}` filled in), `panels.py` (`[[panel]]` of a type: trend, value, lamp, gauge; each type's keys, parser and HTML), `page.py` (`render`), `_config.py` (table helpers); the page's look is `web/static/dashboard.css`; standard library only (its sources need their extras). To add a panel type: its keys in `PANEL_KEYS`, a parser in `PANELS`, a drawer in `DRAW`, its CSS. Terms (`docs/config.md`): dashboard, source, id, section, panel, plot, range, element; "signals view" is the `` ` `` dialog, not a panel |
| `src/sightglass/plc.py` | PLC types for sources that read PLCs: Structured Text `TYPE` declarations, TwinCAT 3 memory layout (`pack_mode`), value decode/encode, TOML interface files; standard library only |
| `src/sightglass/web/` | aiohttp server (`/`, `/ws`, `/ws/signals`, `POST /update`, `GET /values`, `GET /history`) and `static/`: `sightglass.js` (data-bind client: text, `state`, `width`, `sparkline`, `var`, `data-stale-after`, theme switches, the `` ` `` key), `plot.js` (`data-plot`: a canvas plot from `GET /history`, loaded only on pages that have one; round value ticks with `data-plot-units` / `data-plot-scale`; 2 to 8 ids in a validated palette, light or dark by the plot's background, with a legend), `signals.js` + `signals.css` (the signals view, with its `Page` line from `monitor:message` and `monitor:plot` events: a `<dialog>` in a shadow root, loaded on first open, streaming `/ws/signals` only while open), `instruments.css` (LCD/LED styles), `terminal.css` (the terminal theme's fonts, colours and CRT effects; `/_sightglass/theme.js` is generated per dashboard), `auto.html` (generic page), fonts |
| `src/sightglass/terminal.py` | full-screen terminal output |
| `src/sightglass/client.py` | stdlib-only HTTP client for other processes |
| `src/sightglass/cli.py`, `demo.py` | the `sightglass` command and `--demo` data |
| `src/sightglass/launch/` | `--demo launch`, the showcase: `page.html` + `static/` (plain HTML/CSS), `screen.py` (its terminal screen, `--terminal`), `vehicle.py` (flight model, in-process source), `ground.py` and `weather.py` (feeder processes), `mission.py` (shared clock); loaded only by that demo |
| `src/sightglass/load/` | `--demo load`: `plc.py` (the fake PLC's program, its own process, exits with its parent), `__init__.py` (`Meter`: a source measuring the dashboard it runs in), `page.html` (readings, heat map, lines, glitch log); loaded only by that demo |
| `src/sightglass/fake_plc.py` | a fake TwinCAT PLC on pyads' test server (127.0.0.1:48898): memory, symbol table, symbol version, whole AMS frames; for `tests/test_ads.py` and the load demo |
| `src/sightglass/plot/` | `--demo plot`: `page.html` (two `data-plot`s) and 100 simulated waves with spikes; loaded only by that demo |
| `src/sightglass/GUIDE.md` | the agent guide (imported above, printed by `--guide`) |
| `examples/` | the gallery (`examples/README.md`): robot dashboard (segment displays, serial), numbered single-file examples, terminal, ZeroMQ publisher, Docker, `fake_device.py` (virtual serial port), `plc.toml` (a commented ADS interface file) |
| `docs/` | the reference the README links to (`sources.md`, `config.md`, `pages.md`, `performance.md`, `extending.md`), its images; `screenshot.py` retakes the launch demo's four (page in both themes, its signals view, terminal screen) and the plot demo's (`screenshot.py plot` for that one alone) |

## Invariants

- **Never block the event loop.** Blocking I/O (serial, ZeroMQ, stdin) runs on
  a thread through `sources/_thread.thread_items`; the loop only decodes and
  applies.
- **Thread safety lives in `Monitor`.** Public mutators called off the loop
  thread are handed to it (`_loop_elsewhere`, `_on_loop`); internal state is
  only touched on the loop thread. Keep new mutators on that path.
- **Bad input never raises out of a source.** Decoders reject with
  rate-limited logging (`Decoder._reject`); the monitor rejects per id,
  logged once (`Monitor._reject`).
- **Optional dependencies stay optional.** Core modules import only the
  standard library; `SerialSource`, `ZmqSource`, `AdsSource` and
  `WebDashboard` are loaded lazily in `__init__.py`; `client.py` must not
  import aiohttp, zmq, serial or pyads (a test checks).
- **Writes to devices are guarded.** A value reaches a device only through
  `Monitor` (a source's `claims`/`write`), only with
  `SIGHTGLASS_ALLOW_WRITES=1` (checked in `Monitor._write` and again by the
  source) and the source's own flag, and over HTTP only with a token.
  Refused writes answer 4xx, never 5xx (`Client` retries 5xx; a write must
  not arrive late). Keep any new write path behind the same checks.
- **Be visible.** The dashboard prints its address; the package never
  installs a `NullHandler`; setup errors are one clear line, not a traceback.
- **Pages are HTML.** Display logic is `data-bind` / `data-mode` in
  `sightglass.js`; no per-page JavaScript. New element kinds need a
  `to_json` shape the generic page (`auto.html`) knows how to draw.

## Tests

- Prefer end to end: pseudo-terminal serial ports (`tests/conftest.py`),
  real ZeroMQ sockets, real WebSockets (`aiohttp.test_utils`), the CLI as a
  subprocess, and a real browser for `sightglass.js` (`tests/test_browser.py`,
  marked `browser`). Use `port=0` and `announce=False` for dashboards in tests.
- Browser tests use Playwright's async API. Not pytest-playwright: its sync
  fixtures run their own event loop and break every pytest-asyncio test that
  runs after them.
- `filterwarnings = error`: close every socket, pipe, file and HTTP error
  response, or the test fails.
- ADS tests (`tests/test_ads.py`) talk real ADS to `sightglass/fake_plc.py`, a fake
  PLC (memory, symbol table, symbol version) behind pyads' test server on
  127.0.0.1:48898, which must be free. They skip on Windows, where pyads
  needs TwinCAT's router.
- Serial tests need pseudo-terminals and skip on Windows. CI runs Linux,
  macOS and Windows on Python 3.11 to 3.14; check behaviour that differs
  between versions (e.g. `subprocess`, `asyncio`) on 3.11 too.
- A bug fix starts with a test that fails without it.

## Before finishing a change

- Run the tests and ruff.
- UI changes: look at the generic page (`sightglass --demo`), the launch demo
  (`sightglass --demo launch`) and the robot example in a browser, at desktop
  and 390 px widths, the first two in both themes (`--theme terminal`), each
  with its signals view open (`` ` ``); the
  launch's terminal screen (`--terminal`) at 120 x 36 and 80 x 24. Changes to
  any of these: retake the README screenshots.
- API or docs changes: run every snippet in `README.md` and `GUIDE.md` as
  written, in a clean virtualenv; they are user-facing contracts.
- Releases: see `CONTRIBUTING.md` (tag `v<__version__>`, trusted publishing).
