"""REST polling source (requires ``aiohttp``): values from JSON over HTTP GET."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import KW_ONLY, dataclass
from functools import lru_cache
from typing import Any

import aiohttp

from ..monitor import Update
from ._poll import Failed, Feed, parse, poll, read_body

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

    ``interval`` overrides the source's, for a slow-changing endpoint. A reply
    with a status outside 2xx is ignored unless listed in ``accept``: a health
    check that answers 503 when something is down is still data.
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
        await poll(
            lambda: self._fetch(session, endpoint),
            endpoint.interval or self.interval,
            queue,
            endpoint.url,
            feed,
        )

    async def _fetch(
        self, session: aiohttp.ClientSession, endpoint: Endpoint
    ) -> Update:
        try:
            async with session.get(endpoint.url) as response:
                if not response.ok and response.status not in endpoint.accept:
                    raise Failed(f"HTTP {response.status} {response.reason}")
                body = await read_body(response, self.max_bytes)
        except (aiohttp.ClientError, TimeoutError) as error:
            raise Failed(str(error) or type(error).__name__) from None
        return await parse(len(body), _document, body, endpoint)

    def __repr__(self) -> str:
        urls = ", ".join(e.url for e in self.endpoints)
        return f"RestSource({urls})"


def _document(body: bytes, endpoint: Endpoint) -> Update:
    """The ids in a reply's JSON: all of a poll's parsing, so it can be run off
    the event loop."""
    try:
        document = json.loads(body)
    except ValueError as error:
        raise Failed(f"not JSON: {error}") from None
    return extract(document, endpoint)


def extract(document: Any, endpoint: Endpoint) -> Update:
    """The ``{id: value}`` an endpoint's JSON document stands for; ``null`` is
    None, for invalid data."""
    for part in filter(None, endpoint.select.split(".")):
        if isinstance(document, Mapping) and part in document:
            document = document[part]
        elif (
            isinstance(document, list) and part.isdigit() and int(part) < len(document)
        ):
            document = document[int(part)]
        else:
            raise Failed(f"no {endpoint.select!r} in the reply")
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
    def compile(patterns: tuple[str, ...]) -> re.Pattern[str] | None:
        if not patterns:
            return None
        parts = (
            "".join(".*" if c == "*" else re.escape(c) for c in p) for p in patterns
        )
        return re.compile("|".join(f"(?:{part})" for part in parts), re.DOTALL)

    wanted, unwanted = compile(include), compile(exclude)

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
