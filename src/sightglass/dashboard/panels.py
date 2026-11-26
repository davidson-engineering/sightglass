"""The panels a config file can name: ``[[panel]]`` of a ``type``.

A panel is one tile of the page. Its type says what it shows:

``trend``  one or more ids plotted against time, a plot per range
``value``       one id's current value as large text
``lamp``        one id as an on/off light
``gauge``       one id as a bar between a minimum and a maximum

Each type has its keys (:data:`PANEL_KEYS`), a function that reads a table
into a :class:`Panel` (:data:`PANELS`) and one that draws it as HTML
(:data:`DRAW`). The first draws from history, the others from live values, as
every ``data-bind`` on a page does.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, replace
from typing import Any

from ..elements import Element, TextElement
from ..formatting import TextFormat
from ._config import only

COMMON = {"type", "title", "each"}
PANEL_KEYS = {
    "trend": COMMON | {"ids", "ranges", "mode"},
    "value": COMMON | {"id", "units", "precision"},
    "lamp": COMMON | {"id"},
    "gauge": COMMON | {"id", "units", "precision", "min", "max"},
}
PANEL_TYPES = tuple(PANEL_KEYS)
MODES = ("lines", "heatmap")
COLOURS = 4  # range-0 ... range-3 in dashboard.css; more repeat
WEIGHT = {"trend": 3}  # how much of a section's height a panel's row asks for


@dataclass(frozen=True)
class Panel:
    """A tile. ``ids`` are id patterns for a ``trend`` (``*`` for any
    characters) and the one id for every other type."""

    type: str
    title: str
    ids: tuple[str, ...]
    ranges: tuple[float, ...] = ()  # trend: seconds, longest first
    mode: str = "lines"  # trend: "lines" or "heatmap"
    units: str = ""  # value, gauge
    precision: int | None = None  # value, gauge: decimals shown
    low: float = 0.0  # gauge: the value at the bar's empty end
    high: float = 100.0  # gauge: and at its full end

    @property
    def live(self) -> bool:
        """Whether it needs the page's live-values connection."""
        return self.type != "trend"

    def elements(self) -> list[Element]:
        """The monitor elements it needs declared, if it needs any: a number
        shown to a fixed number of decimals."""
        if self.precision is None:
            return []
        return [TextElement(self.ids[0], format=TextFormat(precision=self.precision))]


def build(table: dict[str, Any], index: int) -> list[Panel]:
    """The panels a ``[[panel]]`` table stands for: one, or one per name in
    ``each``."""
    where = f"[[panel]] {index + 1}"
    kind = table.get("type", "trend")
    if kind not in PANEL_TYPES:
        raise ValueError(
            f"type in {where} must be {' or '.join(PANEL_TYPES)}, not {kind!r}"
        )
    only(table, PANEL_KEYS[kind], where)
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
    panel = Panel(kind, title, **PANELS[kind](table, where))
    return [panel if name is None else _named(panel, name) for name in names]


def _named(panel: Panel, name: str) -> Panel:
    """``panel`` with ``{name}`` in its title and ids replaced by ``name``."""
    return replace(
        panel,
        title=panel.title.replace("{name}", name),
        ids=tuple(i.replace("{name}", name) for i in panel.ids),
    )


def _trend(table: dict[str, Any], where: str) -> dict[str, Any]:
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
    return {
        "ids": tuple(ids),
        "ranges": tuple(sorted(set(ranges), reverse=True)),
        "mode": mode,
    }


def _one_id(table: dict[str, Any], where: str) -> tuple[str, ...]:
    """The ``id`` of a panel that shows a single value."""
    id = table.get("id")
    if not isinstance(id, str) or not id:
        raise ValueError(f"{where} needs an id")
    if "*" in id:
        raise ValueError(f"id in {where} names one id; a plot takes patterns (ids)")
    return (id,)


def _reading(table: dict[str, Any], where: str) -> dict[str, Any]:
    """The units and decimals of a panel that shows a number."""
    units = table.get("units", "")
    if not isinstance(units, str):
        raise ValueError(f"units in {where} must be text")
    precision = table.get("precision")
    if precision is not None and (
        isinstance(precision, bool) or not isinstance(precision, int) or precision < 0
    ):
        raise ValueError(f"precision in {where} must be a whole number, 0 or more")
    return {"units": units, "precision": precision}


def _value(table: dict[str, Any], where: str) -> dict[str, Any]:
    return {"ids": _one_id(table, where), **_reading(table, where)}


def _lamp(table: dict[str, Any], where: str) -> dict[str, Any]:
    return {"ids": _one_id(table, where)}


def _gauge(table: dict[str, Any], where: str) -> dict[str, Any]:
    low = table.get("min", 0)
    high = table.get("max", 100)
    if any(isinstance(n, bool) or not isinstance(n, int | float) for n in (low, high)):
        raise ValueError(f"min and max in {where} must be numbers")
    if low >= high:
        raise ValueError(f"min in {where} must be below max")
    return {
        "ids": _one_id(table, where),
        **_reading(table, where),
        "low": float(low),
        "high": float(high),
    }


PANELS = {
    "trend": _trend,
    "value": _value,
    "lamp": _lamp,
    "gauge": _gauge,
}


# -- drawing -------------------------------------------------------------------


def span(seconds: float) -> str:
    """``300`` as "5 minutes", ``90`` as "90 seconds"."""
    for unit, size in (("hour", 3600), ("minute", 60), ("second", 1)):
        if seconds % size == 0 and (seconds >= size):
            count = int(seconds // size)
            return f"{count} {unit}" + ("" if count == 1 else "s")
    return f"{seconds:.15g} seconds"


def draw(panel: Panel) -> str:
    """The panel as HTML for ``sightglass.js`` and ``plot.js`` to fill in."""
    return DRAW[panel.type](panel)


def _heading(panel: Panel) -> str:
    return f"<h3>{html.escape(panel.title)}</h3>" if panel.title else ""


def _frame(panel: Panel, body: str) -> str:
    return (
        f'    <article class="panel {panel.type}">\n      {_heading(panel)}\n'
        f"{body}\n    </article>"
    )


def _draw_trend(panel: Panel) -> str:
    plots = []
    for i, seconds in enumerate(panel.ranges):
        classes = f"plot range-{i % COLOURS}" + (
            " last" if i == len(panel.ranges) - 1 else ""
        )
        mode = ' data-plot-mode="heatmap"' if panel.mode == "heatmap" else ""
        ids = html.escape(" ".join(panel.ids), quote=True)
        plots.append(
            f'      <div class="{classes}" data-plot="{ids}"{mode} '
            f'data-seconds="{seconds:.15g}"></div>'
        )
    return _frame(panel, "\n".join(plots))


def _reading_html(panel: Panel) -> str:
    id = html.escape(panel.ids[0], quote=True)
    units = f"<small>{html.escape(panel.units)}</small>" if panel.units else ""
    return f'      <p class="reading"><span data-bind="{id}"></span>{units}</p>'


def _draw_value(panel: Panel) -> str:
    return _frame(panel, _reading_html(panel))


def _draw_lamp(panel: Panel) -> str:
    id = html.escape(panel.ids[0], quote=True)
    return _frame(
        panel, f'      <div class="light" data-bind="{id}" data-mode="state"></div>'
    )


def _draw_gauge(panel: Panel) -> str:
    id = html.escape(panel.ids[0], quote=True)
    bar = (
        f'      <div class="bar" style="--min: {panel.low:.15g}; '
        f'--max: {panel.high:.15g}">'
        f'<div class="fill" data-bind="{id}" data-mode="var"></div></div>'
    )
    return _frame(panel, f"{_reading_html(panel)}\n{bar}")


DRAW = {
    "trend": _draw_trend,
    "value": _draw_value,
    "lamp": _draw_lamp,
    "gauge": _draw_gauge,
}
