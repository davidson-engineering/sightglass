"""REST polling source (requires ``aiohttp``): values from JSON over HTTP GET."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import suppress
from dataclasses import KW_ONLY, dataclass
from functools import cmp_to_key, lru_cache
from string import Formatter
from typing import Any

import aiohttp

from ..elements import Element, LogMonitor
from ..monitor import Update
from ._poll import (
    Failed,
    Feed,
    Rates,
    compile_patterns,
    describe,
    parse,
    poll,
    read_body,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Endpoint:
    """One URL to poll, and how its JSON becomes ids.

    ``prefix`` is put before every id (``"metrics"`` gives ``metrics.process.
    memory_mb``), so endpoints that share field names don't collide. ``select``
    starts from a place inside the document, as a dotted path
    (``"data.sensors"``). A list of objects is addressed by each object's
    ``key`` field, or by position if it has none: with ``key="id"``,
    ``{"sensors": [{"id": "s0", "rate": 30}]}`` gives ``sensors.s0.rate``.

    A list's ``key`` field names its item, so it is left out of the ids (no
    ``sensors.s0.id``). ``include`` and ``exclude`` are lists of id patterns
    (``*`` stands for any characters), matched against the whole id, prefix
    included: only ids that match an ``include`` (all, if there is none) and no
    ``exclude`` are kept, so the rest never reach a page or ``max_elements``.

    ``rate`` lists ids or patterns of counters that only grow (requests served,
    bytes sent): each shows the change per second between polls, under its own
    id. The first poll, a count that went down (a restart) and a value that is
    not a number show nothing.

    ``interval`` overrides the source's, for a slow-changing endpoint. A reply
    with a status outside 2xx is ignored unless listed in ``accept``: a health
    check that answers 503 when something is down is still data.

    A list of events (the last N, newest last) is not a set of values: with
    ``events`` (the dotted path to the list, counted from ``select``; ``""`` for
    a reply that is the list itself) each event becomes one line of text sent to
    one :class:`~sightglass.LogMonitor`, named ``id`` or else ``prefix``, and
    makes no ids of its own. Only events whose ``cursor`` field is greater than
    the last one seen are sent, oldest first, so each appears once (numbers
    compare as numbers, anything else as text, so ISO times sort); the first
    poll sends them all, and a reply whose newest cursor is lower than the last
    seen is a service that restarted, and is followed from the start. An event
    without the cursor field is skipped. ``line`` is a ``str.format`` template
    over the event's fields (``"{time} {level}: {message}"``; a missing field is
    empty); the default is the event as compact JSON. The reply's other fields
    become ids as usual.
    """

    url: str
    _: KW_ONLY
    prefix: str = ""
    select: str = ""
    key: str = "id"
    interval: float | None = None
    accept: tuple[int, ...] = ()
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    rate: tuple[str, ...] = ()
    events: str | None = None
    cursor: str = "id"
    line: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if self.events is None:
            if self.line or self.id:
                raise ValueError("line and id go with events")
            return
        if not (self.id or self.prefix):
            raise ValueError("events needs an id or a prefix to name the log")
        if not self.cursor:
            raise ValueError("cursor must name a field")
        try:
            list(Formatter().parse(self.line))
        except ValueError as error:
            raise ValueError(f"line is not a format string: {error}") from None

    @property
    def log(self) -> str:
        """The id of the log the events go to."""
        return self.id or self.prefix


class RestSource:
    """Polls JSON endpoints with GET and feeds their values to the monitor.

    Nested objects become dotted ids, like :class:`~sightglass.JsonDecoder`;
    numbers, text and booleans are shown, and empty lists and objects are
    skipped. Each endpoint is polled on its own schedule, so a slow one doesn't
    delay the others. A failing endpoint is logged once when it goes down and
    once when it returns, and polled as usual meanwhile.

    ``null`` means invalid data, and invalid data is never left showing: an id
    that was valid and is now ``null``, missing from the reply, or from an
    endpoint that can't be reached, is blanked, so text shows nothing and a
    plot shows a gap, until a value returns. An id that has never been valid
    gets no display at all. (Bars and sparklines can't take a blank: they keep
    their last value, and ``data-stale-after`` can dim them.)

    ``endpoints`` are URLs or :class:`Endpoint` objects. ``headers`` go with
    every request (``{"Authorization": "Bearer ..."}``, an API key). Requests
    that take longer than ``timeout`` seconds count as failed.
    """

    max_bytes = 16 * 1024 * 1024  # larger replies are rejected, not read in full

    def __init__(
        self,
        *endpoints: str | Endpoint,
        interval: float = 1.0,
        headers: Mapping[str, str] | None = None,
        timeout: float = 5.0,
    ) -> None:
        if not endpoints:
            raise ValueError("RestSource needs at least one URL")
        if interval <= 0:
            raise ValueError("interval must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.endpoints = [Endpoint(e) if isinstance(e, str) else e for e in endpoints]
        for endpoint in self.endpoints:
            if endpoint.interval is not None and endpoint.interval <= 0:
                raise ValueError(f"interval of {endpoint.url} must be positive")
        self.interval = interval
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.feeds = [Feed(endpoint.url) for endpoint in self.endpoints]
        self.elements: list[Element] = [
            LogMonitor(endpoint.log, lines=20)
            for endpoint in self.endpoints
            if endpoint.events is not None
        ]
        self._seen: dict[int, list[Any]] = {}  # per endpoint: the last cursor

    async def updates(self) -> AsyncIterator[list[Update]]:
        queue: asyncio.Queue[Update] = asyncio.Queue()
        async with aiohttp.ClientSession(
            headers=self.headers, timeout=aiohttp.ClientTimeout(total=self.timeout)
        ) as session:
            tasks = [
                asyncio.create_task(self._poll(session, endpoint, queue, feed))
                for endpoint, feed in zip(self.endpoints, self.feeds, strict=True)
            ]
            try:
                while True:
                    batch = [await queue.get()]
                    while not queue.empty():  # replies that arrived together
                        batch.append(queue.get_nowait())
                    yield batch
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _poll(
        self,
        session: aiohttp.ClientSession,
        endpoint: Endpoint,
        queue: asyncio.Queue[Update],
        feed: Feed,
    ) -> None:
        seen = self._seen.setdefault(id(endpoint), [None])

        async def fetch() -> Update:
            values, lines = await self._fetch(session, endpoint, seen)
            for line in lines:
                queue.put_nowait({endpoint.log: line})
            return values

        await poll(
            fetch,
            endpoint.interval or self.interval,
            queue,
            endpoint.url,
            feed,
            Rates(endpoint.rate) if endpoint.rate else None,
        )

    async def _fetch(
        self, session: aiohttp.ClientSession, endpoint: Endpoint, seen: list[Any]
    ) -> tuple[Update, list[str]]:
        try:
            async with session.get(endpoint.url) as response:
                if not response.ok and response.status not in endpoint.accept:
                    raise Failed(
                        f"HTTP {response.status} {response.reason}; its body was "
                        f"ignored (accept = [{response.status}] shows it)"
                    )
                body = await read_body(response, self.max_bytes)
        except (aiohttp.ClientError, TimeoutError) as error:
            raise Failed(describe(error)) from None
        return await parse(len(body), _document, body, endpoint, seen)

    async def snapshot(self) -> Update:
        """Every endpoint polled once: the ids they make, with their values.
        Raises :class:`Failed`, naming the URL, if one can't be read."""
        values: dict[str, Any] = {}
        async with aiohttp.ClientSession(
            headers=self.headers, timeout=aiohttp.ClientTimeout(total=self.timeout)
        ) as session:
            for endpoint in self.endpoints:
                try:
                    found, lines = await self._fetch(session, endpoint, [None])
                    values.update(found)
                    if endpoint.events is not None:
                        values[endpoint.log] = lines[-1] if lines else ""
                except Failed as error:
                    raise Failed(f"{endpoint.url}: {error}") from None
        return values

    def __repr__(self) -> str:
        urls = ", ".join(e.url for e in self.endpoints)
        return f"RestSource({urls})"


def _document(
    body: bytes, endpoint: Endpoint, seen: list[Any]
) -> tuple[Update, list[str]]:
    """The ids and the new event lines in a reply's JSON: all of a poll's
    parsing, so it can be run off the event loop. ``seen`` holds the last event
    cursor, and is moved on."""
    try:
        document = json.loads(body)
    except ValueError as error:
        raise Failed(f"not JSON: {error}") from None
    values = extract(document, endpoint)
    if endpoint.events is None:
        return values, []
    return values, new_events(document, endpoint, seen)


def _walk(document: Any, path: str) -> Any:
    for part in filter(None, path.split(".")):
        if isinstance(document, Mapping) and part in document:
            document = document[part]
        elif (
            isinstance(document, list) and part.isdigit() and int(part) < len(document)
        ):
            document = document[int(part)]
        else:
            raise Failed(f"no {path!r} in the reply")
    return document


def _without(node: Any, parts: list[str]) -> Any:
    """``node`` without what ``parts`` lead to, copying only along the path."""
    if not parts:
        return {}
    if isinstance(node, Mapping):
        return {
            k: _without(v, parts[1:]) if k == parts[0] else v for k, v in node.items()
        }
    return node


def new_events(document: Any, endpoint: Endpoint, seen: list[Any]) -> list[str]:
    """The lines for the events in ``document`` that come after the cursor in
    ``seen[0]``, oldest first; ``seen`` is moved to the newest cursor."""
    found = _walk(_walk(document, endpoint.select), endpoint.events or "")
    if not isinstance(found, list):
        raise Failed(f"{endpoint.events!r} in the reply is not a list")
    events = [
        e for e in found if isinstance(e, Mapping) and _cursor(e, endpoint) is not None
    ]
    if not events:
        return []
    events.sort(
        key=cmp_to_key(
            lambda a, b: _compare(_cursor(a, endpoint), _cursor(b, endpoint))
        )
    )
    newest = _cursor(events[-1], endpoint)
    last = seen[0]
    if last is not None and _compare(newest, last) < 0:
        last = None  # the service restarted: follow it from the start
    seen[0] = newest
    return [
        _line(e, endpoint.line)
        for e in events
        if last is None or _compare(_cursor(e, endpoint), last) > 0
    ]


def _cursor(event: Mapping[Any, Any], endpoint: Endpoint) -> Any:
    """An event's cursor, or None if it has none it can be ordered by."""
    value = event.get(endpoint.cursor)
    if value is None or isinstance(value, bool | list | dict) or value == "":
        return None
    return value


def _compare(a: Any, b: Any) -> int:
    """-1, 0 or 1: as numbers if both are, else as text."""
    if not (isinstance(a, int | float) and isinstance(b, int | float)):
        a, b = str(a), str(b)
    return (a > b) - (a < b)


class _Fields(dict[str, Any]):
    """An event's fields for ``format_map``: absent or null is empty."""

    def __getitem__(self, name: str) -> Any:
        value = self.get(name)
        return "" if value is None else value


def _line(event: Mapping[Any, Any], template: str) -> str:
    text = None
    if template:
        with suppress(AttributeError, IndexError, TypeError, ValueError):
            text = template.format_map(_Fields((str(k), v) for k, v in event.items()))
    if text is None:
        text = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
    return " ".join(text.splitlines())


def extract(document: Any, endpoint: Endpoint) -> Update:
    """The ``{id: value}`` an endpoint's JSON document stands for; ``null`` is
    None, for invalid data. The list of ``events``, if any, makes no ids."""
    document = _walk(document, endpoint.select)
    if endpoint.events:
        document = _without(document, endpoint.events.split("."))
    elif endpoint.events == "":
        document = {}
    values: dict[str, Any] = {}
    _flatten(document, endpoint.prefix, endpoint.key, values, drop_key=True)
    if endpoint.include or endpoint.exclude:
        keep = _matcher(endpoint.include, endpoint.exclude)
        return {name: value for name, value in values.items() if keep(name)}
    return values


@lru_cache(maxsize=64)
def _matcher(
    include: tuple[str, ...], exclude: tuple[str, ...]
) -> Callable[[str], bool]:
    wanted, unwanted = compile_patterns(include), compile_patterns(exclude)

    def keep(name: str) -> bool:
        return (wanted is None or wanted.fullmatch(name) is not None) and (
            unwanted is None or unwanted.fullmatch(name) is None
        )

    return keep


_LEAVES = frozenset({str, int, float, bool})  # what a JSON document is made of
_SCALARS = (str, int, float, bool)
_INDEX = tuple(map(str, range(256)))  # a list's positions as text, made once


def _flatten(
    node: Any, path: str, key: str, out: dict[str, Any], drop_key: bool = False
) -> None:
    """Add the scalars under ``node`` to ``out``; ids are ``path`` plus names.
    With ``drop_key``, an item of a list that is named by its ``key`` field has
    no id for that field.

    Run on the event loop for every poll, so a document's own types (dict, list,
    str, int, float, bool) take the short way, and a leaf is never a call."""
    cls = type(node)
    in_list = False
    if cls is dict:
        named = node.items()
    elif cls is list:
        named = zip(_names(node, key), node, strict=True)
        in_list = drop_key
    elif isinstance(node, Mapping):
        named = ((str(name), value) for name, value in node.items())
    elif isinstance(node, list):
        named = zip(_names(node, key), node, strict=True)
        in_list = drop_key
    else:
        if path and (node is None or isinstance(node, _SCALARS)):
            out[path] = node
        return
    prefix = f"{path}." if path else ""
    for name, value in named:
        if type(name) is not str:
            name = str(name)
        cls = type(value)
        if cls in _LEAVES or value is None:
            out[prefix + name] = value
        elif in_list and isinstance(value, Mapping) and _item_key(value, key):
            rest = {k: v for k, v in value.items() if k != key}
            _flatten(rest, prefix + name, key, out, drop_key)
        else:
            _flatten(value, prefix + name, key, out, drop_key)


def _names(items: list[Any], key: str) -> list[str]:
    """The id part for each item of a list: its ``key`` field, or its position."""
    return [
        _item_name(item, key, index)
        if isinstance(item, Mapping)
        else (_INDEX[index] if index < 256 else str(index))
        for index, item in enumerate(items)
    ]


def _item_key(item: Mapping[Any, Any], key: str) -> bool:
    name = item.get(key)
    return isinstance(name, str | int) and name != ""


def _item_name(item: Any, key: str, index: int) -> str:
    name = item.get(key) if isinstance(item, Mapping) else None
    return str(name) if isinstance(name, str | int) and name != "" else str(index)
