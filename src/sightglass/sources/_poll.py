"""What the polling sources (REST, InfluxDB) share: reading a whole reply, and
the loop that polls on a schedule and blanks what stopped being valid."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import aiohttp

from ..monitor import Update

logger = logging.getLogger(__name__)

BLANK = ""  # shows as nothing, and is a gap, not a number, in a plot


class Failed(Exception):
    """A poll that produced no values; the message says why."""


async def read_body(response: aiohttp.ClientResponse, limit: int) -> bytes:
    """The whole body of ``response``. A longer one is refused, not cut short."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > limit:
            raise Failed(f"reply over {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def poll(
    fetch: Callable[[], Awaitable[Update]],
    every: float,
    queue: asyncio.Queue[Update],
    name: str,
) -> None:
    """Call ``fetch`` every ``every`` seconds and queue what it returns.

    An id that was valid and is now ``None``, missing, or from a ``fetch`` that
    failed, is queued as :data:`BLANK` once, so nothing stale stays on show. A
    failure is logged when it starts (and again if its cause changes) and when
    it ends, not on every poll; any
    exception counts as one, so nothing a reply holds can end the polling."""
    loop = asyncio.get_running_loop()
    down = False
    logged = ""  # the failure last warned about
    showing: set[str] = set()  # the ids with a valid value on display
    due = loop.time()
    while True:
        try:
            reply = await fetch()
            reason = None
        except Failed as failure:
            reason = str(failure)
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
        if reason is not None:
            reply = {}
            if reason != logged:  # a new cause is worth a line too
                logger.warning("%s: %s; will keep trying", name, reason)
                logged = reason
            down = True
        else:
            if down:
                logger.info("%s is answering again", name)
            down = False
            logged = ""
        valid = {id: value for id, value in reply.items() if value is not None}
        update = {**valid, **dict.fromkeys(showing - valid.keys(), BLANK)}
        showing = set(valid)
        if update:
            queue.put_nowait(update)
        # Due one interval after the last was due, so a slow reply doesn't
        # lower the rate; after a long stall, start counting from now.
        due = max(due + every, loop.time())
        await asyncio.sleep(due - loop.time())
