"""Every value a monitor has accepted, by id, with its last minute: what the
dashboard's signals panel shows, for checking what a feed actually sends.

A signal is an id as it was sent (``"X.velocity"``, ``"table.row.column"``)
and its value as sent, before an element scales or formats it. History is
kept in time slots shared by every signal, so their charts line up: each slot
holds the lowest and highest value the signal had during it, and a value that
didn't change carries on through the slots that follow. Values that aren't
numbers have no history; true/false (and on/off, yes/no) chart as 1/0.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from .formatting import default_text
from .history import Point, Track, chart_number

__all__ = ["RESOLUTION", "SLOTS", "Point", "Signal", "Signals", "chart_number"]

RESOLUTION = 0.5
"""Seconds per slot."""

SLOTS = 120
"""Slots kept per signal: a minute."""


def slot_at(now: float) -> int:
    """The slot that ``now`` (a ``monotonic()`` time) falls in."""
    return math.floor(now / RESOLUTION)


class Signal:
    """One id's latest value and its last :data:`SLOTS` slots."""

    __slots__ = ("number", "track", "updated", "value", "version")

    def __init__(self, slot: int) -> None:
        self.value: Any = None
        self.number: float | None = None  # the latest value, charted
        self.updated = 0.0
        self.version = 0
        self.track = Track(SLOTS, slot)

    def record(self, value: Any, slot: int, now: float, version: int) -> None:
        """``value`` arrived at ``now``, in ``slot`` (:func:`slot_at`)."""
        self.number = chart_number(value)
        self.track.record(self.number, slot)
        self.value = value
        self.updated, self.version = now, version

    def trace(self, first: int, last: int) -> list[Point]:
        """Slots ``first`` to ``last``, inclusive."""
        return self.track.points(first, last)

    def to_json(self, first: int, last: int, now: float) -> dict[str, Any]:
        return {
            "text": default_text(self.value),
            "number": self.number,
            "age": max(now - self.updated, 0.0),
            "trace": self.trace(first, last),
        }


class Signals:
    """The signals a monitor has accepted (:attr:`Monitor.signals`), recorded
    on its event loop's thread."""

    def __init__(self) -> None:
        self._signals: dict[str, Signal] = {}

    def __len__(self) -> int:
        return len(self._signals)

    def __contains__(self, id: object) -> bool:
        return id in self._signals

    def __getitem__(self, id: str) -> Signal:
        return self._signals[id]

    def record(
        self, values: Iterable[tuple[str, Any]], now: float, version: int
    ) -> None:
        """Record ``(id, value)`` pairs that arrived at ``now`` (``monotonic()``),
        in the monitor's ``version``."""
        slot = slot_at(now)
        for id, value in values:
            signal = self._signals.get(id)
            if signal is None:
                signal = self._signals[id] = Signal(slot - 1)
            signal.record(value, slot, now, version)

    def snapshot(self, now: float) -> dict[str, Any]:
        """Every signal with its last minute, ending with the slot of ``now``."""
        last = slot_at(now)
        first = last - SLOTS + 1
        return {
            "resolution": RESOLUTION,
            "slots": SLOTS,
            "slot": last,
            "from": first,
            "signals": {
                id: signal.to_json(first, last, now)
                for id, signal in self._signals.items()
            },
        }

    def changes(self, version: int, first: int, now: float) -> dict[str, Any]:
        """The signals recorded after ``version``, with slots ``first`` to the
        slot of ``now``."""
        last = slot_at(now)
        return {
            id: signal.to_json(first, last, now)
            for id, signal in self._signals.items()
            if signal.version > version
        }
