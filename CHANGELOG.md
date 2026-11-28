# Changelog

What changed in each release, for people who use sightglass. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/). Releases before 0.6.0 are
described on the project's GitHub releases page.

## [Unreleased]

## [0.6.0] - 2026-11-27

### Breaking

- A REST list of objects no longer makes an id of each object's key field: `sensors.s0.id` is gone, and the key is in the other ids' names (`sensors.s0.rate`).
- A `TextElement` or `Coordinate` with `units` now has them in its text, in `GET /values` and on pages; a page or script that added the units itself shows them twice, so drop its own.

### Added

- `ProcessSource` shows the program it runs in: CPU, memory, threads, open files, uptime and garbage collector counts.
- `LogHandler` sends Python log records to a `LogMonitor`, on a dashboard or through a `Client`; `data-mode="log"` binds one on a page of your own.
- `Counters`, an element for a row of labelled counts.
- REST event feeds: `events`, `cursor`, `line` and `id` show each new event as one line of a log, with no id made per event, and a config file's `log` panel shows them.
- REST counters as rates: `rate` shows a counter that only grows as its change per second.
- REST `include` and `exclude` choose which ids an endpoint makes.
- `--list` polls a source once, prints each id with its value and exits, so a config file's panels can be written from it.
- A config file needs no `[[source]]`: feed it with `POST /update`, or serve it from your own program.
- The generic page shows a trend for each numeric id when the dashboard keeps history (`--history`).
- Plots (`data-plot`) have round value ticks, `data-plot-units` and `data-plot-scale`, and a colour and legend for each of two to eight ids.
- Python 3.15 is supported and tested; sightglass runs on 3.11 to 3.15.
- Guides for monitoring your own Python app and a web service (metrics, health check, event feed), with a runnable web service example.

### Changed

- An unchanged value is sent to open pages as its id alone, which cuts their traffic (by about 30% for 915 ids polled five times a second).
- A `Sparkline` keeps a whole number whole: `36`, not `36.0`.
- Ids dropped at `max_elements` are summarised once a minute with how many ids were dropped, instead of a warning for each.
- REST failure and recovery messages say what happened in plain words, recovery is logged as a warning, and a 503 warning names `accept`.
- A config panel whose ids match nothing is warned about, and panel errors name the panel's section and title.
- A `LogMonitor` takes the full width of the generic page.

### Fixed

- The generic terminal layout no longer runs past the bottom of the screen when an element takes several lines.
- The signals view no longer clips ids, so ids that differ only at the end can be told apart on a phone.
- A config page's header no longer shows `vspike` for a version that isn't a number.
- A monitor started with no outputs keeps running until `stop()`, instead of ending at once and losing values sent from other threads.
- The project's `.gitignore` now keeps `.env` files out of commits.
