"""A source that generates values, for demos and testing without hardware."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from ..monitor import Update


class SimulatedSource:
    """Calls ``generate(seconds_elapsed)`` every ``interval`` seconds and feeds
    the returned mapping into the monitor."""

    def __init__(
        self, generate: Callable[[float], Update], *, interval: float = 0.05
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.generate = generate
        self.interval = interval

    async def updates(self) -> AsyncIterator[list[Update]]:
        loop = asyncio.get_running_loop()
        start = due = loop.time()
        while True:
            yield [self.generate(loop.time() - start)]
            # Each update is due one interval after the last was due, so the
            # time making and applying values takes doesn't slow them down.
            # Fallen far behind (a stall), it starts counting again from now.
            due = max(due + self.interval, loop.time() - self.interval)
            await asyncio.sleep(due - loop.time())

    def __repr__(self) -> str:
        return f"SimulatedSource(interval={self.interval})"
