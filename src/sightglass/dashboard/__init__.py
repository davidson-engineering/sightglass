"""Dashboards described by a TOML file: ``sightglass --config FILE``.

A config names where the values come from and what to show, so a dashboard is
a file and not a program::

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
    range = 60
    secondary_range = 300

A **source** is where values come from (``sources.py``). A **panel** is one
tile of the page, of a **type** (``panels.py``): ``trend`` plots ids over
time (a primary plot and, optionally, a secondary one), ``value`` shows one
id as large text, ``lamp`` as a light and ``gauge`` as a bar. A **section** is
a titled group of panels (``[[section]]`` with
``[[section.panel]]`` inside); panels written at the top level are one
untitled section. A file with no ``[[source]]`` is fed like any dashboard,
by ``POST /update``, :class:`~sightglass.Client` or ``monitor.set()``.

The file says what the dashboard is; whoever runs it says where::

    monitor = load("dashboard.toml").start(port=8080)
    monitor.set("app.cpu", 12.5)

See :func:`load` for every key. Standard library only; the sources it builds
need their extras (``rest`` needs aiohttp), and serving it needs ``web``.
"""

from __future__ import annotations

import asyncio
import difflib
import fnmatch
import hashlib
import logging
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from ..elements import Element
from ..monitor import MAX_ELEMENTS, THEMES, TITLE, Monitor, Source
from . import sources
from ._config import number, only, tables
from .page import render
from .panels import Panel, build

if TYPE_CHECKING:
    from ..web import WebDashboard

__all__ = ["Dashboard", "Panel", "Section", "load", "render"]

logger = logging.getLogger(__name__)

TOP_KEYS = {
    "title",
    "version",
    "history",
    "max_elements",
    "panel_width",
    "title_bar",
    "theme",
    "stale_after",
    "source",
    "section",
    "panel",
}
SECTION_KEYS = {"title", "panel"}
TITLE_BARS = ("top", "bottom")


@dataclass(frozen=True)
class Section:
    """A titled group of panels, laid out in a row that wraps."""

    title: str
    panels: tuple[Panel, ...]


@dataclass
class Dashboard:
    """What a config file describes. :meth:`page` is its HTML."""

    title: str
    history: float
    max_elements: int
    panel_width: int
    sources: list[Source] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    version: str = ""  # the dashboard's own, from the file
    title_bar: str = "top"  # where the title bar goes: "top" or "bottom"
    theme: str = THEMES[0]  # the page's look unless a viewer switches
    stale_after: float | None = None  # seconds without data before values dim
    config: str = ""  # the file's name
    digest: str = ""  # a short fingerprint of the file's text: it changes when it does
    started: str = ""  # when this instance was loaded

    @property
    def panels(self) -> list[Panel]:
        """Every panel, section by section."""
        return [panel for section in self.sections for panel in section.panels]

    def page(self) -> str:
        return render(self)

    def plotted(self) -> list[str]:
        """Every id pattern a panel plots: all the history the page needs."""
        return sorted(
            {i for panel in self.panels if panel.type == "trend" for i in panel.ids}
        )

    def elements(self) -> list[Element]:
        """The monitor elements the panels need declared, one per id."""
        found: dict[str, Element] = {}
        for panel in self.panels:
            for element in panel.elements():
                found.setdefault(element.id, element)
        return list(found.values())

    def monitor(self) -> Monitor:
        """A monitor as the file describes: its elements, its limit, and
        history for the ids its panels plot (only those: the rest of a
        source's ids would cost 8 bytes a slot each for plots that never draw
        them)."""
        from ..history import History

        monitor = Monitor(max_elements=self.max_elements)
        monitor.add(*self.elements())  # e.g. a number shown to fixed decimals
        monitor.history = History(self.history, only=self.plotted())
        return monitor

    def unmatched(self, ids: list[str]) -> list[str]:
        """One warning for each panel with an id or pattern that matches none
        of ``ids``, with the closest id when there is one."""
        warnings = []
        for section in self.sections:
            for panel in section.panels:
                missing = [
                    pattern
                    for pattern in panel.ids
                    if not fnmatch.filter(ids, pattern.replace("[", "[[]"))
                ]
                if not missing:
                    continue
                where = f'panel "{panel.title}"'
                if section.title:
                    where += f' (section "{section.title}")'
                text = ", ".join(f"'{pattern}'" for pattern in missing)
                message = f"{where}: no id {text}"
                close = difflib.get_close_matches(missing[0], ids, 1)
                if close:
                    message += f"; did you mean '{close[0]}'?"
                warnings.append(message)
        return warnings

    def audit(self) -> Audit | None:
        """An output that warns about panels whose ids match nothing, once
        every source has answered; ``None`` unless every source polls (the
        others can deliver an id at any time, so nothing is known to be
        missing)."""
        per_source = [getattr(source, "feeds", None) for source in self.sources]
        if not per_source or not all(per_source):
            return None
        return Audit(self, [feed for feeds in per_source for feed in feeds])

    def web(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8080,
        token: str | None = None,
        debug: bool = False,
        open_browser: bool = False,
        announce: bool = True,
    ) -> WebDashboard:
        """The dashboard's page, served where the caller says. Its title,
        theme and ``stale_after`` come from the file."""
        from ..web import WebDashboard

        return WebDashboard(
            html=self.page(),
            host=host,
            port=port,
            title=self.title,
            theme=self.theme,
            token=token,
            stale_after=self.stale_after,
            debug=debug,
            open_browser=open_browser,
            announce=announce,
        )

    def start(self, **where: Any) -> Monitor:
        """Serve the dashboard in the background, reading its sources, and
        return its monitor, for ``set()`` and ``stop()``. ``where`` is
        :meth:`web`'s: ``host``, ``port``, ``token`` and so on."""
        monitor = self.monitor()
        outputs: list[Any] = [self.web(**where)]
        if audit := self.audit():
            outputs.append(audit)
        monitor.start(sources=self.sources, outputs=outputs)
        return monitor


class Answering(Protocol):
    """What a polling source's feed offers the audit (``sources._poll.Feed``)."""

    async def first_answer(self) -> None: ...


class Audit:
    """An output that, once every polling source has had a successful poll,
    warns once for each panel that shows no id (``Dashboard.unmatched``)."""

    SETTLE = 0.25  # seconds for the first replies' values to be applied

    def __init__(self, dashboard: Dashboard, feeds: list[Answering]) -> None:
        self.dashboard = dashboard
        self.feeds = feeds

    async def run(self, monitor: Monitor) -> None:
        await asyncio.gather(*(feed.first_answer() for feed in self.feeds))
        await asyncio.sleep(self.SETTLE)
        for warning in self.dashboard.unmatched(monitor.signals.ids()):
            logger.warning("%s", warning)
        await asyncio.Event().wait()  # an output runs until cancelled


def load(path: str | Path) -> Dashboard:
    """Read a config file. Raises :class:`ValueError` with a one-line reason for
    anything wrong with it.

    Top level: ``title``, ``version`` (a label for this dashboard's own version,
    shown beside the title), ``history`` (seconds of values to keep for plots;
    default: the longest range and a minute), ``max_elements`` (default 2000),
    ``panel_width`` (least pixels wide a panel is; default 520), ``title_bar``
    (``"top"``, the default, or ``"bottom"``: where the title and what
    identifies this instance go), ``theme`` (``"classic"``, the default, or
    ``"terminal"``: the look unless a viewer switches), ``stale_after``
    (seconds without new data before values dim; default: never).

    The file says what the dashboard is; the command line says where and how
    it runs (``--host``, ``--port``, ``--token``, ``--open``, ``--debug``).

    ``[[source]]``, of a ``type``: ``rest`` (``url`` (required), ``prefix``,
    ``select``, ``key``, ``interval``, ``timeout``, ``accept`` and ``headers``;
    see :class:`~sightglass.sources.rest.Endpoint`), ``ads`` (``target`` and
    ``variables``, or ``file``), ``zmq``, ``serial`` (each with a ``decoder``)
    and ``influxdb`` (see :class:`~sightglass.sources.influx.InfluxSource`).
    Any text in a source may hold ``${NAME}`` or ``${NAME:-default}``,
    replaced by that environment variable, so a secret such as a token
    never has to be in the file; ``$${`` writes a literal ``${``.

    ``[[panel]]``, of a ``type``, each with a ``title`` and ``each`` (a list
    of names that makes one panel per name, with ``{name}`` in the title and
    ids replaced by it): ``trend`` (the default: ``ids``, id patterns
    with ``*`` for any characters; ``range``, the primary plot's seconds,
    default 60; ``secondary``, whether there is a secondary plot, default
    true; ``secondary_range``, its seconds, default 300; ``secondary_ratio``,
    its height over the primary's, default 0.5; ``first``, which is on top,
    ``"secondary"`` (the default) or ``"primary"``; ``mode``, ``"lines"`` or
    ``"heatmap"``), ``value`` (``id``, ``units``,
    ``precision``), ``lamp`` (``id``) and ``gauge`` (``id``, ``units``,
    ``precision``, ``min``, ``max``). Panels go at the top level or in
    ``[[section]]`` tables with a ``title``, as ``[[section.panel]]``.
    """
    path = Path(path)
    raw = path.read_bytes()
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise ValueError(f"{path}: not UTF-8 text") from None
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path}: {error}") from None
    try:
        dashboard = _build(data, path.parent)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from None
    dashboard.config = path.name
    dashboard.digest = hashlib.sha256(raw).hexdigest()[:8]
    dashboard.started = datetime.now().strftime("%Y-%m-%d %H:%M")
    return dashboard


def _build(data: dict[str, Any], base: Path) -> Dashboard:
    only(data, TOP_KEYS, "the config")
    found = _sections(data)
    panels = [panel for section in found for panel in section.panels]
    if not panels:
        raise ValueError("needs at least one [[panel]]")
    built = [
        sources.build(table, i, base) for i, table in enumerate(tables(data, "source"))
    ]
    longest = max((plot.seconds for p in panels for plot in p.plots), default=0)
    history = number(data, "history", longest + 60, "the config")
    if history < longest:
        raise ValueError(
            f"history is {history:.15g} s but a panel plots {longest:.15g} s"
        )
    max_elements = int(number(data, "max_elements", MAX_ELEMENTS, "the config"))
    panel_width = int(number(data, "panel_width", 520, "the config"))
    title = data.get("title", TITLE)
    version = data.get("version", "")
    if (
        not isinstance(title, str)
        or isinstance(version, bool)
        or not isinstance(version, str | int | float)
    ):
        raise ValueError("title and version must be text")
    title_bar = data.get("title_bar", "top")
    if title_bar not in TITLE_BARS:
        raise ValueError(
            f"title_bar must be {' or '.join(TITLE_BARS)}, not {title_bar!r}"
        )
    theme = data.get("theme", THEMES[0])
    if theme not in THEMES:
        raise ValueError(f"theme must be {' or '.join(THEMES)}, not {theme!r}")
    stale_after = (
        number(data, "stale_after", 1, "the config") if "stale_after" in data else None
    )
    _one_format_each(panels)
    return Dashboard(
        title,
        history,
        max_elements,
        panel_width,
        built,
        found,
        version=str(version),
        title_bar=title_bar,
        theme=theme,
        stale_after=stale_after,
    )


def _one_format_each(panels: list[Panel]) -> None:
    """Refuse two panels that show an id to different numbers of decimals: the
    monitor formats an id once."""
    seen: dict[str, int] = {}
    for panel in panels:
        if panel.precision is None:
            continue
        if seen.setdefault(panel.ids[0], panel.precision) != panel.precision:
            raise ValueError(
                f"{panel.ids[0]} is shown to {seen[panel.ids[0]]} and "
                f"{panel.precision} decimals; give its panels one precision"
            )


def _sections(data: dict[str, Any]) -> list[Section]:
    """The sections of a config: ``[[section]]`` tables, or the panels at the
    top level as one section without a title."""
    top = tables(data, "panel")
    if top and "section" in data:
        raise ValueError(
            "put every [[panel]] in a [[section]] (as [[section.panel]]), or none"
        )
    if top:
        return [Section("", _panels(top))]
    found = []
    for i, table in enumerate(tables(data, "section")):
        where = f"[[section]] {i + 1}"
        only(table, SECTION_KEYS, where)
        title = table.get("title", "")
        if not isinstance(title, str):
            raise ValueError(f"title in {where} must be text")
        if panels := _panels(tables(table, "panel")):
            found.append(Section(title, panels))
        else:
            raise ValueError(f"{where} needs at least one [[section.panel]]")
    return found


def _panels(found: list[dict[str, Any]]) -> tuple[Panel, ...]:
    return tuple(p for i, table in enumerate(found) for p in build(table, i))
