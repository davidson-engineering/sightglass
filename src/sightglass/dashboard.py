"""Dashboards described by a TOML file: ``sightglass --config FILE``.

A config names where the values come from and how they are plotted, so a
dashboard is a file and not a program::

    title = "Plant"

    [[source]]
    type = "rest"
    url = "http://localhost:8000/api/readings"
    prefix = "plant"
    select = "sensors"
    interval = 0.5

    [[panel]]
    each = ["North", "South"]
    title = "{name}"
    ids = ["plant.{name}.value.*"]
    ranges = [300, 60]

Each ``[[panel]]`` is a card with one plot per range (seconds), longest at the
top, the shortest biggest. See :func:`load` for every key. Standard library
only; the sources it builds need their extras (``rest`` needs aiohttp).
"""

from __future__ import annotations

import html
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .monitor import Source

TOP_KEYS = {"title", "history", "max_elements", "card_width", "source", "panel"}
SOURCE_TYPES = ("rest",)
REST_KEYS = {
    "type",
    "url",
    "prefix",
    "select",
    "key",
    "interval",
    "timeout",
    "accept",
    "headers",
}
PANEL_KEYS = {"title", "ids", "ranges", "mode", "each"}
MODES = ("lines", "heatmap")
COLOURS = 4  # range-0 ... range-3 in dashboard.css; more repeat
ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


@dataclass(frozen=True)
class Panel:
    """A card: a title and a plot of ``ids`` over each of ``ranges`` seconds."""

    title: str
    ids: tuple[str, ...]
    ranges: tuple[float, ...]  # longest first
    mode: str = "lines"


@dataclass
class Dashboard:
    """What a config file describes. :meth:`page` is its HTML."""

    title: str
    history: float
    max_elements: int
    card_width: int
    sources: list[Source] = field(default_factory=list)
    panels: list[Panel] = field(default_factory=list)

    def page(self) -> str:
        return render(self)


def load(path: str | Path) -> Dashboard:
    """Read a config file. Raises :class:`ValueError` with a one-line reason for
    anything wrong with it.

    Top level: ``title``, ``history`` (seconds of values to keep for plots;
    default: the longest range and a minute), ``max_elements`` (default 2000),
    ``card_width`` (least pixels wide a card is; default 520).

    ``[[source]]`` (``type = "rest"``): ``url`` (required), ``prefix``,
    ``select``, ``key``, ``interval``, ``timeout``, ``accept`` and ``headers``;
    see :class:`~sightglass.sources.rest.Endpoint`. Any text in a source may
    hold ``${NAME}`` or ``${NAME:-default}``, replaced by that environment
    variable, so a secret such as a token never has to be in the file.

    ``[[panel]]``: ``title``, ``ids`` (id patterns, ``*`` for any characters),
    ``ranges`` (seconds, default ``[300, 60]``), ``mode`` (``"lines"`` or
    ``"heatmap"``), and ``each``, a list of names that makes one panel per name,
    with ``{name}`` in the title and ids replaced by it.
    """
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path}: {error}") from None
    try:
        return _build(data)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from None


def _build(data: dict[str, Any]) -> Dashboard:
    _only(data, TOP_KEYS, "the config")
    panels = [
        p for i, table in enumerate(_tables(data, "panel")) for p in _panels(table, i)
    ]
    if not panels:
        raise ValueError("needs at least one [[panel]]")
    sources = [_source(table, i) for i, table in enumerate(_tables(data, "source"))]
    if not sources:
        raise ValueError("needs at least one [[source]]")
    longest = max(r for panel in panels for r in panel.ranges)
    history = _number(data, "history", longest + 60, "the config")
    if history < longest:
        raise ValueError(f"history is {history:g} s but a panel plots {longest:g} s")
    max_elements = int(_number(data, "max_elements", 2000, "the config"))
    card_width = int(_number(data, "card_width", 520, "the config"))
    title = data.get("title", "Dashboard")
    if not isinstance(title, str):
        raise ValueError("title must be text")
    return Dashboard(title, history, max_elements, card_width, sources, panels)


def _tables(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    tables = data.get(key, [])
    if not isinstance(tables, list) or not all(isinstance(t, dict) for t in tables):
        raise ValueError(f"write each {key} as a [[{key}]] table")
    return tables


def _only(table: dict[str, Any], allowed: set[str], where: str) -> None:
    if unknown := sorted(set(table) - allowed):
        raise ValueError(
            f"{where} has unknown {', '.join(map(repr, unknown))}; "
            f"it takes {', '.join(sorted(allowed))}"
        )


def _number(table: dict[str, Any], key: str, default: float, where: str) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"{key} in {where} must be a number above 0")
    return value


def _source(table: dict[str, Any], index: int) -> Source:
    where = f"[[source]] {index + 1}"
    kind = table.get("type", "rest")
    if kind not in SOURCE_TYPES:
        raise ValueError(
            f"{where}: type must be {' or '.join(SOURCE_TYPES)}, not {kind!r}"
        )
    _only(table, REST_KEYS, where)
    table = _expand(table, where)
    if not isinstance(table.get("url"), str) or not table["url"]:
        raise ValueError(f"{where} needs a url")
    from . import Endpoint, RestSource

    accept = table.get("accept", [])
    if not isinstance(accept, list) or not all(isinstance(s, int) for s in accept):
        raise ValueError(f"accept in {where} must be a list of status codes")
    headers = table.get("headers", {})
    if not isinstance(headers, dict) or not all(
        isinstance(v, str) for v in headers.values()
    ):
        raise ValueError(f"headers in {where} must be a table of text")
    endpoint = Endpoint(
        table["url"],
        prefix=str(table.get("prefix", "")),
        select=str(table.get("select", "")),
        key=str(table.get("key", "id")),
        interval=_number(table, "interval", 1.0, where)
        if "interval" in table
        else None,
        accept=tuple(accept),
    )
    return RestSource(
        endpoint,
        interval=endpoint.interval or 1.0,
        headers=headers,
        timeout=_number(table, "timeout", 5.0, where),
    )


def _expand(value: Any, where: str) -> Any:
    """``value`` with ``${NAME}`` / ``${NAME:-default}`` filled in, throughout."""
    if isinstance(value, str):

        def fill(match: re.Match[str]) -> str:
            name, default = match.groups()
            if (found := os.environ.get(name)) is not None:
                return found
            if default is not None:
                return default
            raise ValueError(f"{where}: the environment variable {name} is not set")

        return ENV.sub(fill, value)
    if isinstance(value, dict):
        return {key: _expand(item, where) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item, where) for item in value]
    return value


def _panels(table: dict[str, Any], index: int) -> list[Panel]:
    where = f"[[panel]] {index + 1}"
    _only(table, PANEL_KEYS, where)
    ids = table.get("ids")
    if (
        not isinstance(ids, list)
        or not ids
        or not all(isinstance(i, str) and i for i in ids)
    ):
        raise ValueError(f"{where} needs ids, a list of id patterns")
    ranges = table.get("ranges", [300, 60])
    if (
        not isinstance(ranges, list)
        or not ranges
        or not all(
            isinstance(r, int | float) and not isinstance(r, bool) and r > 0
            for r in ranges
        )
    ):
        raise ValueError(f"ranges in {where} must be a list of seconds above 0")
    mode = table.get("mode", "lines")
    if mode not in MODES:
        raise ValueError(f"mode in {where} must be {' or '.join(MODES)}, not {mode!r}")
    title = table.get("title", "")
    if not isinstance(title, str):
        raise ValueError(f"title in {where} must be text")
    names = table.get("each")
    if names is None:
        names = [None]
    elif (
        not isinstance(names, list)
        or not names
        or not all(isinstance(n, str) for n in names)
    ):
        raise ValueError(f"each in {where} must be a list of names")
    ordered = tuple(sorted(set(ranges), reverse=True))
    return [
        Panel(
            _name(title, name),
            tuple(_name(i, name) for i in ids),
            ordered,
            mode,
        )
        for name in names
    ]


def _name(text: str, name: str | None) -> str:
    return text if name is None else text.replace("{name}", name)


def span(seconds: float) -> str:
    """``300`` as "5 minutes", ``90`` as "90 seconds"."""
    for unit, size in (("hour", 3600), ("minute", 60), ("second", 1)):
        if seconds % size == 0 and (seconds >= size):
            count = int(seconds // size)
            return f"{count} {unit}" + ("" if count == 1 else "s")
    return f"{seconds:g} seconds"


def render(dashboard: Dashboard) -> str:
    """The page: a card per panel, plain HTML that sightglass.js and plot.js
    draw into, in the style of ``dashboard.css``."""
    title = html.escape(dashboard.title)
    shared = {panel.ranges for panel in dashboard.panels}
    key = ""
    if len(shared) == 1:  # a legend only when every panel plots the same ranges
        ranges = next(iter(shared))
        parts = (
            f'<b class="range-{i % COLOURS}">{span(r)}</b>'
            for i, r in enumerate(ranges)
        )
        key = f"<span>top to bottom: {' &middot; '.join(parts)}</span>"
    cards = []
    for panel in dashboard.panels:
        plots = []
        for i, seconds in enumerate(panel.ranges):
            classes = f"plot range-{i % COLOURS}" + (
                " last" if i == len(panel.ranges) - 1 else ""
            )
            mode = ' data-plot-mode="heatmap"' if panel.mode == "heatmap" else ""
            ids = html.escape(" ".join(panel.ids), quote=True)
            plots.append(
                f'      <div class="{classes}" data-plot="{ids}"{mode} '
                f'data-seconds="{seconds:g}"></div>'
            )
        cards.append(
            f"    <section>\n      <h2>{html.escape(panel.title)}</h2>\n"
            + "\n".join(plots)
            + "\n    </section>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="/_sightglass/dashboard.css">
</head>
<body>
  <header>
    <h1>{title}</h1>
    {key}
  </header>
  <div class="grid" style="--card-width: {dashboard.card_width}px">
{chr(10).join(cards)}
  </div>
  <script src="/_sightglass/sightglass.js"></script>
</body>
</html>
"""
