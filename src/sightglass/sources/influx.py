"""InfluxDB 2.x source (requires ``aiohttp``): the latest values of a bucket."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import aiohttp

from ..monitor import Update
from ._poll import Failed, parse, poll, read_body

logger = logging.getLogger(__name__)

DEFAULT_ID_FROM = ("_measurement", "_field")


class InfluxSource:
    """Polls an InfluxDB 2.x bucket with Flux and shows each series' newest value.

    Every ``interval`` seconds the query runs and the newest point of every
    series (a measurement, field and tag set) is sent to the monitor under an id
    made from ``id_from``, the names of the columns to join with dots; the
    default is ``("_measurement", "_field")``, giving ``cell.voltage``. Add a
    tag to tell series apart: ``("_measurement", "sensor", "_field")``. ``prefix``
    goes before every id.

    Either give ``query``, Flux that ends with one row per series (``last()``),
    or let one be written from ``bucket`` and, to narrow it, ``measurement``,
    ``fields`` (a list) and ``tags`` (``{"site": "north"}``).

    A series with no point in the last ``window`` seconds is not current: it is
    blanked, so text shows nothing and a plot shows a gap, as with a ``null``
    from :class:`~sightglass.sources.rest.RestSource`. The same happens to every
    series while the server can't be reached. A series that has never had a point
    gets no display. A query's own ``range()`` sets the window when ``query`` is
    given.

    ``url`` is the server (``"http://localhost:8086"``), ``org`` its
    organisation (name or id) and ``token`` an API token that can read the bucket.
    """

    max_bytes = 64 * 1024 * 1024  # larger replies are rejected, not read in full

    def __init__(
        self,
        url: str,
        *,
        org: str,
        token: str,
        bucket: str = "",
        query: str = "",
        measurement: str = "",
        fields: Sequence[str] = (),
        tags: Mapping[str, str] | None = None,
        window: float = 60.0,
        id_from: Sequence[str] = DEFAULT_ID_FROM,
        prefix: str = "",
        interval: float = 1.0,
        timeout: float = 10.0,
    ) -> None:
        if not url or not org or not token:
            raise ValueError("InfluxSource needs a url, an org and a token")
        if bool(bucket) == bool(query):
            raise ValueError("give a bucket, or a query, and not both")
        if interval <= 0 or timeout <= 0 or window <= 0:
            raise ValueError("interval, timeout and window must be positive")
        if not id_from:
            raise ValueError("id_from needs at least one column")
        self.url = url.rstrip("/")
        self.org = org
        self.token = token
        self.query = query or flux(bucket, measurement, fields, tags or {}, window)
        # Without a dialect the server sends no #datatype rows, and every value
        # would be text.
        self._request = {
            "query": self.query,
            "type": "flux",
            "dialect": {"annotations": ["datatype"], "header": True},
        }
        self.id_from = tuple(id_from)
        self.prefix = prefix
        self.interval = interval
        self.timeout = timeout

    async def updates(self) -> AsyncIterator[list[Update]]:
        queue: asyncio.Queue[Update] = asyncio.Queue()
        headers = {
            "Authorization": f"Token {self.token}",
            "Accept": "application/csv",
        }
        async with aiohttp.ClientSession(
            headers=headers, timeout=aiohttp.ClientTimeout(total=self.timeout)
        ) as session:
            task = asyncio.create_task(self._poll(session, queue))
            try:
                while True:
                    yield [await queue.get()]
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _poll(
        self, session: aiohttp.ClientSession, queue: asyncio.Queue[Update]
    ) -> None:
        await poll(lambda: self._fetch(session), self.interval, queue, self.url)

    async def _fetch(self, session: aiohttp.ClientSession) -> Update:
        try:
            async with session.post(
                f"{self.url}/api/v2/query",
                params={"org": self.org},
                json=self._request,
            ) as response:
                body = await read_body(response, self.max_bytes)
                if not response.ok:
                    raise Failed(
                        f"HTTP {response.status} {_message(body) or response.reason}"
                    )
        except (aiohttp.ClientError, TimeoutError) as error:
            raise Failed(str(error) or type(error).__name__) from None
        return await parse(len(body), _reply, body, self.id_from, self.prefix)

    def __repr__(self) -> str:
        return f"InfluxSource({self.url})"


def flux(
    bucket: str,
    measurement: str,
    fields: Sequence[str],
    tags: Mapping[str, str],
    window: float,
) -> str:
    """The Flux that asks for the newest point of every series in ``bucket``
    within the last ``window`` seconds."""
    lines = [
        f"from(bucket: {_string(bucket)})",
        f"|> range(start: -{round(window * 1000)}ms)",
    ]
    if measurement:
        lines.append(f"|> filter(fn: (r) => r._measurement == {_string(measurement)})")
    if fields:
        test = " or ".join(f"r._field == {_string(name)}" for name in fields)
        lines.append(f"|> filter(fn: (r) => {test})")
    for name, value in tags.items():
        lines.append(f"|> filter(fn: (r) => r[{_string(name)}] == {_string(value)})")
    lines.append("|> last()")
    return "\n  ".join(lines)


def _string(text: str) -> str:
    """``text`` as a Flux string literal."""
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("${", "\\${")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _reply(body: bytes, id_from: Sequence[str], prefix: str) -> Update:
    """The ids in a reply: all of a poll's parsing, so it can be run off the
    event loop."""
    return rows(body.decode("utf-8", errors="replace"), id_from, prefix)


def rows(text: str, id_from: Sequence[str], prefix: str = "") -> Update:
    """The ``{id: value}`` in Flux's annotated CSV; an empty value is None.

    A reply is a run of tables, each with its own header, apart by blank lines.
    Rows that lack a column named in ``id_from`` are skipped."""
    values: dict[str, Any] = {}
    header: list[str] | None = None
    types: list[str] = []
    for row in csv.reader(io.StringIO(text, newline="")):
        if not any(row):
            header, types = None, []
            continue
        if row[0].startswith("#"):
            if row[0] == "#datatype":
                types = row
            continue
        if header is None:
            header = row
            continue
        if header[:2] == ["error", "reference"]:
            raise Failed(f"the query failed: {row[0]}")
        if "_value" not in header:
            continue
        cells = dict(zip(header, row, strict=False))
        id = ".".join(
            part
            for part in (prefix, *(cells.get(name, "") for name in id_from))
            if part
        )
        if id:
            at = header.index("_value")
            values[id] = _convert(cells["_value"], types[at] if at < len(types) else "")
    return values


def _convert(text: str, kind: str) -> Any:
    """A cell as the type its column declares (or, with none declared, the
    type its text looks like); None for no value."""
    if text == "":
        return None
    if not kind:
        return _infer(text)
    try:
        if kind in ("double", "float"):
            return float(text)
        if kind in ("long", "unsignedLong"):
            return int(text)
    except ValueError:
        return None
    if kind == "boolean":
        return text == "true"
    return text


def _infer(text: str) -> Any:
    if text in ("true", "false"):
        return text == "true"
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


def _message(body: bytes) -> str:
    """The reason in an InfluxDB error reply, ``{"code": ..., "message": ...}``."""
    try:
        return str(json.loads(body)["message"])
    except (ValueError, KeyError, TypeError):
        return ""
