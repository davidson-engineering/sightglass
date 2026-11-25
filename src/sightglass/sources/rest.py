"""REST polling source (requires ``aiohttp``): values from JSON over HTTP GET."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

import aiohttp

from ..monitor import Update

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

    ``interval`` overrides the source's, for a slow-changing endpoint. A reply
    with a status outside 2xx is ignored unless listed in ``accept``: a health
    check that answers 503 when something is down is still data.
    """

    url: str
    prefix: str = ""
    select: str = ""
    key: str = "id"
    interval: float | None = None
    accept: tuple[int, ...] = ()


class RestSource:
    """Polls JSON endpoints with GET and feeds their values to the monitor.

    Nested objects become dotted ids, like :class:`~sightglass.JsonDecoder`;
    numbers, text and booleans are shown, and ``null`` and empty lists are
    skipped. Each endpoint is polled on its own schedule, so a slow one doesn't
    delay the others. A failing endpoint is logged once when it goes down and
    once when it returns, and polled as usual meanwhile.

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

    async def updates(self) -> AsyncIterator[list[Update]]:
        queue: asyncio.Queue[Update] = asyncio.Queue()
        async with aiohttp.ClientSession(
            headers=self.headers, timeout=aiohttp.ClientTimeout(total=self.timeout)
        ) as session:
            tasks = [
                asyncio.create_task(self._poll(session, endpoint, queue))
                for endpoint in self.endpoints
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
    ) -> None:
        loop = asyncio.get_running_loop()
        every = endpoint.interval or self.interval
        down = False
        due = loop.time()
        while True:
            try:
                update = await self._fetch(session, endpoint)
            except _Failed as failure:
                if not down:
                    logger.warning("%s: %s; will keep trying", endpoint.url, failure)
                down = True
            else:
                if down:
                    logger.info("%s is answering again", endpoint.url)
                down = False
                if update:
                    queue.put_nowait(update)
            # Due one interval after the last was due, so a slow reply doesn't
            # lower the rate; after a long stall, start counting from now.
            due = max(due + every, loop.time())
            await asyncio.sleep(due - loop.time())

    async def _fetch(
        self, session: aiohttp.ClientSession, endpoint: Endpoint
    ) -> Update:
        try:
            async with session.get(endpoint.url) as response:
                if not response.ok and response.status not in endpoint.accept:
                    raise _Failed(f"HTTP {response.status} {response.reason}")
                body = await response.content.read(self.max_bytes + 1)
        except (aiohttp.ClientError, TimeoutError) as error:
            raise _Failed(str(error) or type(error).__name__) from None
        if len(body) > self.max_bytes:
            raise _Failed(f"reply over {self.max_bytes} bytes")
        try:
            document = json.loads(body)
        except ValueError as error:
            raise _Failed(f"not JSON: {error}") from None
        return extract(document, endpoint)

    def __repr__(self) -> str:
        urls = ", ".join(e.url for e in self.endpoints)
        return f"RestSource({urls})"


class _Failed(Exception):
    """A poll that produced no values; the message says why."""


def extract(document: Any, endpoint: Endpoint) -> Update:
    """The ``{id: value}`` an endpoint's JSON document stands for."""
    for part in filter(None, endpoint.select.split(".")):
        if isinstance(document, Mapping) and part in document:
            document = document[part]
        elif (
            isinstance(document, list) and part.isdigit() and int(part) < len(document)
        ):
            document = document[int(part)]
        else:
            raise _Failed(f"no {endpoint.select!r} in the reply")
    values: dict[str, Any] = {}
    _flatten(document, endpoint.prefix, endpoint.key, values)
    return values


def _flatten(node: Any, path: str, key: str, out: dict[str, Any]) -> None:
    """Add the scalars under ``node`` to ``out``; ids are ``path`` plus names."""
    if isinstance(node, Mapping):
        children = ((str(name), value) for name, value in node.items())
    elif isinstance(node, list):
        children = (
            (_item_name(item, key, index), item) for index, item in enumerate(node)
        )
    else:
        if path and isinstance(node, str | int | float | bool):
            out[path] = node
        return
    for name, value in children:
        _flatten(value, f"{path}.{name}" if path else name, key, out)


def _item_name(item: Any, key: str, index: int) -> str:
    name = item.get(key) if isinstance(item, Mapping) else None
    return str(name) if isinstance(name, str | int) and name != "" else str(index)
