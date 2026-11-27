"""Each numeric signal's recent history, for value-vs-time plots
(:attr:`Monitor.history`, ``GET /history``).

Values are recorded by the id they were sent to, as sent, like
:mod:`signals`, in time slots shared by every signal (``resolution`` seconds
each, 50 ms by default) so plots line up. A slot holds the lowest and highest
value the signal had during it, and a value that didn't change carries on
through the slots that follow. Values that aren't numbers leave a gap;
true/false (and on/off, yes/no) record as 1/0.

A query reduces any window to at most ``points`` points, each the lowest and
highest value of the slots it covers, so a plot gets about one point per
pixel however long the window is. Points cover whole multiples of their
step, so the same window length always gives the same points and a live plot
can append new ones to those it has.

Times are ``time.perf_counter()``, not ``monotonic()``: on Windows before
Python 3.13, ``monotonic()`` ticks every 15.6 ms, too coarse for 50 ms slots.

Memory: 8 bytes per slot per signal that has sent a number (two float32s),
e.g. 96 MB for 1000 signals at 20 slots a second for 10 minutes. float32 holds
about 7 significant digits, more than a plot can show; exact values are in
``GET /values`` and the signals view.
"""

from __future__ import annotations

import math
import numbers
import re
from array import array
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .formatting import as_number

RESOLUTION = 0.05
"""Seconds per slot by default: 20 a second."""

Point = float | list[float] | None
"""One point: the value, ``[lowest, highest]`` when it varied, or None for no
number."""

_NAN = math.nan
_FLOAT32_MAX = 3.4028234663852886e38
_SWITCHES = {"true": 1.0, "on": 1.0, "yes": 1.0, "false": 0.0, "off": 0.0, "no": 0.0}


def chart_number(value: Any) -> float | None:
    """``value`` as a point on a chart, or None if it isn't a finite number
    (or true/false, on/off, yes/no: 1/0)."""
    if type(value) is float:  # the usual case, first
        return value if math.isfinite(value) else None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        number = as_number(value)
        if number is None:
            return _SWITCHES.get(value.strip().lower())
    elif isinstance(value, numbers.Real):  # NumPy's numbers too
        number = value
    else:
        return None
    try:
        number = float(number)
    except OverflowError:  # an int too big for a float
        return None
    return number if math.isfinite(number) else None


class Track:
    """The last ``slots`` slots of one signal: the lowest and highest number
    in each, in a ring. ``typecode`` is ``"d"`` (float64) or ``"f"`` (float32,
    half the memory, points rounded to the 7 digits it holds)."""

    __slots__ = ("_high", "_limit", "_low", "held", "size", "slot", "typecode")

    def __init__(self, slots: int, slot: int, typecode: str = "d") -> None:
        self.size = slots
        self.typecode = typecode
        self.slot = slot  # the newest slot started
        self.held = _NAN  # the latest number, or NaN after a value that isn't one
        self._limit = _FLOAT32_MAX if typecode == "f" else math.inf
        self._low = array(typecode, [_NAN]) * slots
        self._high = array(typecode, [_NAN]) * slots

    def record(self, number: float | None, slot: int) -> None:
        """``number`` (None: a value that isn't one) arrived in ``slot``."""
        if slot > self.slot:
            self._start(slot)
        else:  # the clock can't go back; if it seems to, it's the newest slot
            slot = self.slot
        if number is None:
            self.held = _NAN
            return
        if not -self._limit <= number <= self._limit:  # too big for float32
            number = math.copysign(self._limit, number)
        i = slot % self.size
        low = self._low[i]
        if math.isnan(low):
            self._low[i] = self._high[i] = number
        elif number < low:
            self._low[i] = number
        elif number > self._high[i]:
            self._high[i] = number
        self.held = number

    def _start(self, slot: int) -> None:
        """Start the slots up to ``slot``, each from the number held before it."""
        size, held = self.size, self.held
        for s in range(max(self.slot + 1, slot - size + 1), slot + 1):
            self._low[s % size] = self._high[s % size] = held
        self.slot = slot

    def points(self, first: int, last: int, step: int = 1) -> list[Point]:
        """Slots ``first`` to ``last`` in points of ``step`` slots, each
        starting at a multiple of ``step`` (so the first point may begin
        before ``first``); the last point ends at ``last``. Slots after the
        newest one started hold the latest number; forgotten ones have none."""
        newest = self.slot
        oldest = newest - self.size + 1
        held = None if math.isnan(self.held) else self.held
        if step == 1 and self.typecode == "d":  # the signals view: direct
            return [self._slot(s, oldest, newest, held) for s in range(first, last + 1)]
        # The kept slots the points cover, copied out of the ring once.
        start = max(first // step * step, oldest)
        stop = min(last, newest)
        lows = self._span(self._low, start, stop)
        highs = self._span(self._high, start, stop)
        rounded = self.typecode == "f"
        result: list[Point] = []
        for b in range(first // step, last // step + 1):
            s0, s1 = b * step, min(b * step + step - 1, last)
            low = high = _NAN
            if s0 <= stop and s1 >= start:
                i, j = max(s0, start) - start, min(s1, stop) - start + 1
                if j - i == 1:  # the usual case at full resolution
                    low, high = lows[i], highs[i]
                else:
                    segment = lows[i:j]
                    total = sum(segment)  # NaN if any slot is a gap; in C, fast
                    if total == total:
                        low, high = min(segment), max(highs[i:j])
                    elif kept := [v for v in segment if v == v]:  # v == v: not NaN
                        low = min(kept)
                        high = max(v for v in highs[i:j] if v == v)
            if s1 > newest and held is not None:
                low = held if math.isnan(low) else min(low, held)
                high = held if math.isnan(high) else max(high, held)
            if math.isnan(low):
                result.append(None)
                continue
            if rounded:
                low, high = float(f"{low:.7g}"), float(f"{high:.7g}")
            result.append(low if low == high else [low, high])
        return result

    def _slot(self, s: int, oldest: int, newest: int, held: float | None) -> Point:
        if s > newest:  # no value since: the latest holds
            return held
        if s < oldest:  # forgotten
            return None
        i = s % self.size
        low, high = self._low[i], self._high[i]
        if low != low:  # NaN: no number
            return None
        return low if low == high else [low, high]

    def _span(self, ring: array, first: int, last: int) -> array:
        """Slots ``first`` to ``last`` (all kept), oldest first."""
        if first > last:
            return array(self.typecode)
        i, j = first % self.size, last % self.size + 1
        return ring[i:j] if i < j else ring[i:] + ring[:j]


def _pattern(patterns: Iterable[str]) -> re.Pattern[str]:
    """One regular expression for any of ``patterns`` (``*``: any characters)."""
    return re.compile(
        "|".join(".*".join(map(re.escape, p.split("*"))) for p in patterns) or "(?!)"
    )


class History:
    """The last ``seconds`` of every signal that has sent a number, in slots
    of ``resolution`` seconds (:attr:`Monitor.history`). Recorded and queried
    on the monitor's event loop thread.

    ``only`` limits it to the ids matching these patterns (``*`` for any
    characters, as in :meth:`matching`): a dashboard that plots a few of its
    thousands of ids needn't keep the rest (8 bytes a slot each)."""

    max_cached_patterns = 256  # patterns come from queries: don't keep them all

    def __init__(
        self,
        seconds: float = 600,
        resolution: float = RESOLUTION,
        only: Iterable[str] | None = None,
    ) -> None:
        if not (seconds > 0 and math.isfinite(seconds)):
            raise ValueError("seconds must be a positive number")
        if not (resolution > 0 and math.isfinite(resolution)):
            raise ValueError("resolution must be a positive number")
        self.seconds = seconds
        self.resolution = resolution
        self.slots = max(round(seconds / resolution), 1)
        self._tracks: dict[str, Track] = {}
        self._only = None if only is None else _pattern(only)
        self._wanted: dict[str, bool] = {}  # id -> whether `only` lets it in
        # pattern -> (how many tracks there were, the ids it matched then)
        self._resolved: dict[str, tuple[int, list[str]]] = {}

    def __len__(self) -> int:
        return len(self._tracks)

    def __contains__(self, id: object) -> bool:
        return id in self._tracks

    def slot_at(self, now: float) -> int:
        """The slot that ``now`` (a ``perf_counter()`` time) falls in."""
        return math.floor(now / self.resolution)

    def record(self, values: Iterable[tuple[str, Any]], now: float) -> None:
        """Record ``(id, value)`` pairs that arrived at ``now`` (``perf_counter()``)."""
        slot = self.slot_at(now)
        tracks = self._tracks
        only, wanted = self._only, self._wanted
        for id, value in values:
            if only is not None:
                keep = wanted.get(id)
                if keep is None:
                    keep = wanted[id] = only.fullmatch(id) is not None
                if not keep:
                    continue
            number = chart_number(value)
            track = tracks.get(id)
            if track is None:
                if number is None:  # no history until it sends a number
                    continue
                track = tracks[id] = Track(self.slots, slot - 1, "f")
            track.record(number, slot)

    def matching(self, ids: Iterable[str]) -> list[str]:
        """``ids`` with each pattern replaced by the ids it matches, in the
        order they first sent a number. In a pattern, ``*`` stands for any
        characters (dots included): ``"wave.*"``, ``"MAIN.*.fPosition"``."""
        result: dict[str, None] = {}
        tracks = len(self._tracks)  # ids are only ever added
        for id in ids:
            if "*" not in id:
                result[id] = None
                continue
            matched = self._resolved.get(id)
            if matched is None or matched[0] != tracks:
                pattern = _pattern([id])
                matched = (tracks, [i for i in self._tracks if pattern.fullmatch(i)])
                if len(self._resolved) >= self.max_cached_patterns:
                    self._resolved.clear()
                self._resolved[id] = matched
            result.update(dict.fromkeys(matched[1]))
        return list(result)

    def window(
        self,
        start: float,
        end: float,
        points: int | None = None,
        *,
        step: float | None = None,
    ) -> Window:
        """The slots from ``start`` to ``end`` (``perf_counter()`` times), in at
        most ``points`` points, or in points of ``step`` seconds (rounded to
        whole slots), e.g. the ``step`` an earlier query returned."""
        if end < start:
            raise ValueError("end must not be before start")
        first, last = self.slot_at(start), self.slot_at(end)
        if step is not None:
            if not (step > 0 and math.isfinite(step)):
                raise ValueError("step must be a positive number of seconds")
            return Window(first, last, max(round(step / self.resolution), 1))
        if points is None or points < 2:
            raise ValueError("points must be at least 2")
        count = last - first + 1
        slots = 1 if count <= points else math.ceil(count / (points - 1))
        return Window(first, last, slots)

    def trace(self, id: str, window: Window) -> list[Point] | None:
        """``id``'s points in ``window``, or None if it has no history."""
        track = self._tracks.get(id)
        if track is None:
            return None
        return track.points(window.first, window.last, window.step)

    def describe(self, window: Window) -> dict[str, Any]:
        """``window`` as sent with its points: ``start``, the ``perf_counter()``
        time the first point begins (at or before the one asked for);
        ``step``, seconds per point; ``points``, how many."""
        return {
            "start": window.first // window.step * window.step * self.resolution,
            "step": round(window.step * self.resolution, 9),
            "points": window.points,
        }

    def query(
        self, ids: Iterable[str], start: float, end: float, points: int
    ) -> dict[str, Any]:
        """``ids`` from ``start`` to ``end`` (``perf_counter()`` times) in at most
        ``points`` points: :meth:`describe` of the window, and ``signals``,
        each id's points (None if it has no history).

        Each id takes about 1 ms per 10,000 slots in the window."""
        window = self.window(start, end, points)
        signals = {id: self.trace(id, window) for id in ids}
        return {**self.describe(window), "signals": signals}


@dataclass(frozen=True)
class Window:
    """Slots ``first`` to ``last``, in points of ``step`` slots that start at
    multiples of ``step`` (:meth:`History.window`)."""

    first: int
    last: int
    step: int

    @property
    def points(self) -> int:
        return self.last // self.step - self.first // self.step + 1
