# Extending sightglass

Each part of sightglass is a small interface you can implement yourself:

- **Element:** subclass `Element` and implement `update(value)`,
  `to_json()` (what the browser receives) and `render(width)` (terminal text).
  `to_json()` returns a new value each call, never a list or dict the
  element goes on changing: each page is sent only values that differ from
  the last it was sent, so one changed in place looks unchanged.
  Override `update_field` to accept `"<id>.<field>"` updates.
- **Wire format:** subclass `LineDecoder` and implement
  `decode_line(line) -> {id: value}`, raising `DecodeError` for bad input; or
  subclass `Decoder` for binary formats.
- **Source:** any object with an async generator method `updates()` that
  yields lists of `{id: value}` mappings. A source that can also write to its
  device adds `claims(id)` (true for its ids) and `async def write(update)`,
  raising `WriteError`; values sent to its ids then go to `write()` (see
  `Source` in `monitor.py`). A list of elements in its `elements` attribute
  says how its ids look: the monitor adds each one whose id the program
  hasn't declared when it starts reading the source (`ProcessSource` does).
- **Output:** any object with `async def run(monitor)`, and optionally
  `async def start(monitor)` for setup that can fail and
  `async def flush(monitor)` to deliver the last values before
  `Monitor.stop()` shuts down. Use
  `await monitor.wait_for_change(version)` and
  `monitor.changes_since(version)` to react to new data, and
  `monitor.ages()` for how long ago each element was updated.


## A source in 20 lines

A source polls a service that isn't JSON over GET, or isn't worth a config
file. This one reads a health URL and shows whether it is up, with a line in a
log for each failure:

```python
import asyncio
import json
import urllib.request

from sightglass import LogMonitor, start


class HealthSource:
    elements = [LogMonitor("events")]  # how its ids look; added if undeclared

    def __init__(self, url, interval=2.0):
        self.url, self.interval = url, interval

    def fetch(self):  # blocking: run in a thread, never on the event loop
        with urllib.request.urlopen(self.url, timeout=3) as reply:
            return json.load(reply)

    async def updates(self):
        while True:
            try:
                up = bool((await asyncio.to_thread(self.fetch))["ok"])
                batch = [{"health.up": up}]
            except Exception as error:  # a source never raises: say, and go on
                batch = [{"health.up": False}, {"events": f"{self.url}: {error}"}]
            yield batch
            await asyncio.sleep(self.interval)


monitor = start(sources=[HealthSource("http://localhost:8000/health")])
```

- **Errors:** anything that goes wrong reading the service (it is down, it
  answered 503, the body isn't what you expected) becomes a value, here
  `health.up` false, and a line of text for a person. An exception that escapes
  `updates()` stops the whole run: `Monitor.run` cancels the other sources and
  the dashboard, and raises it. Catch what the world can do to a poll, and keep the loop.
- **Order:** the mappings in one yielded list are applied in order, and every
  one counts: two `{"events": ...}` in a batch make two lines in the
  `LogMonitor`, where a single mapping holds one value per id. Send events as
  separate mappings, oldest first, not as one joined string.
- **Blocking calls** (`urllib`, a database driver, a serial read) belong in
  `asyncio.to_thread` or a thread of their own
  (`sources/_thread.thread_items`), so the page stays live while they wait.
- For a JSON API, `RestSource` already does this, with timeouts, retries,
  rates and event feeds: see [REST APIs](sources.md#rest-apis).
