# Extending sightglass

Each part of sightglass is a small interface you can implement yourself:

- **Element:** subclass `Element` and implement `update(value)`,
  `to_json()` (what the browser receives) and `render(width)` (terminal text).
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

