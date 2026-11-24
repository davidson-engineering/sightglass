"""A load test you can watch: ``sightglass --demo load``.

    sightglass --demo load --open                         # 1000 signals at 20 Hz
    python -m sightglass.load --signals 2000 --rate 50    # more load

A fake TwinCAT PLC, in a process of its own (load/plc.py), updates
``SIGNALS`` REAL variables ``RATE`` times a second. The dashboard reads them
all over real ADS, as it would a PLC on the network (AdsSource, sum reads of
500), keeps 10 minutes of their history, and serves page.html: every signal
as a heat map over the last 10 minutes, ten of them as lines over the last
minute, and what it costs, measured once a second inside the dashboard's
own process and shown through sightglass itself (the Meter source).

10 to 20 seconds in, and then every 40 to 80 seconds, one signal, at
random, glitches for 100 ms. The log
names it, and the heat map shows it however far it has scrolled.

Needs pyads (``pip install "sightglass[ads]"``) and port 48898 free; Linux
and macOS (on Windows pyads needs TwinCAT's own ADS router).
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from sightglass import (
    LogMonitor,
    Monitor,
    Sparkline,
    TextElement,
    TextFormat,
    WebDashboard,
)

HERE = Path(__file__).parent

SIGNALS = 1000
RATE = 20  # PLC updates (and dashboard reads) a second
HISTORY = 600  # seconds kept
PLC = "127.0.0.1.1.1"  # the fake PLC, on this machine


def build_monitor(signals: int) -> Monitor:
    monitor = Monitor(max_elements=signals + 50, history=HISTORY)
    whole, tenths = TextFormat(precision=0), TextFormat(precision=1)
    monitor.add_group(
        "load",
        [
            TextElement("signals", format=whole),
            TextElement("rate", format=whole),
            TextElement("values", format=whole),
            TextElement("reads", format=tenths),
            Sparkline("cpu", points=120, format=whole),
            Sparkline("loop", points=120, format=tenths),
            TextElement("delay", format=whole),
            TextElement("history", format=whole),
            TextElement("memory", format=whole),
            TextElement("query", format=tenths),
            TextElement("glitches", format=whole),
            LogMonitor("events", lines=8, timestamp=True),
        ],
    )
    return monitor


class Meter:
    """A source that measures the dashboard it runs in, once a second: values
    taken in, CPU, the event loop's worst delay, how long a value takes from
    the PLC to the dashboard, memory, and how long reducing a signal's whole
    history takes. It also logs each glitch the PLC reports."""

    def __init__(self, monitor: Monitor, signals: int, rate: float) -> None:
        self.monitor = monitor
        self.signals = signals
        self.rate = rate
        self.glitches = 0

    async def updates(self):
        loop = asyncio.get_running_loop()
        monitor = self.monitor
        accepted, cpu, started = monitor.accepted, time.process_time(), loop.time()
        sent = 0  # values this meter sent, not counted as load
        while True:
            # A second of 50 ms sleeps: how late does the loop wake us?
            worst = 0.0
            for _ in range(20):
                before = loop.time()
                await asyncio.sleep(0.05)
                worst = max(worst, loop.time() - before - 0.05)
            now = loop.time()
            elapsed = now - started
            values = (monitor.accepted - accepted - sent) / elapsed
            update: dict[str, Any] = {
                "load.signals": self.signals,
                "load.rate": self.rate,
                "load.values": values,
                "load.reads": values / (self.signals + 4),  # the PLC's 4 others
                "load.cpu": (time.process_time() - cpu) / elapsed * 100,
                "load.loop": worst * 1000,
                "load.history": _history_bytes(monitor) / 1e6,
                "load.memory": _peak_memory() / 1e6,
                "load.query": _query_ms(monitor),
            }
            if (delay := _delay_ms(monitor)) is not None:
                update["load.delay"] = delay
            update.update(self._glitch())
            accepted, cpu, started = monitor.accepted, time.process_time(), now
            sent = len(update)
            yield [update]

    def _glitch(self) -> dict[str, Any]:
        """A log line when the PLC has made a new glitch."""
        if "GVL.nGlitches" not in self.monitor:
            return {}
        count = int(self.monitor["GVL.nGlitches"].value or 0)
        if count == self.glitches:
            return {}
        self.glitches = count
        which = int(self.monitor["GVL.nGlitchSignal"].value)
        return {
            "load.glitches": count,
            "load.events": f"glitch {count}: GVL.fSignal{which:04d}, 100 ms",
        }


def _history_bytes(monitor: Monitor) -> int:
    history = monitor.history
    return 0 if history is None else len(history) * history.slots * 8


def _peak_memory() -> int:
    """The process's peak resident memory, in bytes."""
    import resource  # not on Windows, where this demo doesn't run

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024  # Linux: KB


def _delay_ms(monitor: Monitor) -> float | None:
    """From the PLC's latest update (its clock) to the dashboard applying it."""
    if "GVL.fClock" not in monitor:
        return None
    age = monitor.ages()["GVL.fClock"]
    if age is None:
        return None
    applied = time.time() - age
    return (applied - float(monitor["GVL.fClock"].value)) * 1000


def _query_ms(monitor: Monitor) -> float:
    """Milliseconds to reduce one signal's whole history to 600 points."""
    history = monitor.history
    if history is None or "GVL.fSignal0000" not in history:
        return 0.0
    now = time.perf_counter()  # the history's clock
    started = time.perf_counter()
    history.query(["GVL.fSignal0000"], now - HISTORY, now, 600)
    return (time.perf_counter() - started) * 1000


def run(
    *,
    signals: int = SIGNALS,
    rate: float = RATE,
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = False,
    theme: str = "classic",
) -> None:
    """Start the PLC, then serve the dashboard until Ctrl+C."""
    if sys.platform == "win32":
        raise OSError(
            "--demo load needs Linux or macOS: on Windows, pyads needs "
            "TwinCAT's own ADS router"
        )
    try:
        import pyads  # noqa: F401  (needed by the PLC and AdsSource)
    except ImportError:
        raise ImportError(
            '--demo load needs pyads: pip install "sightglass[ads]"'
        ) from None
    if signals < 1 or rate <= 0:
        raise ValueError("signals must be at least 1 and rate more than 0")
    from sightglass import AdsSource

    plc = subprocess.Popen(
        [sys.executable, "-m", "sightglass.load.plc", str(signals), str(rate)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        if plc.stdout.readline().strip() != "ready":
            raise OSError(
                "the demo's PLC didn't start: is port 48898 in use (another "
                "demo, or an ADS router)?"
            )
        monitor = build_monitor(signals)
        web = WebDashboard(
            HERE / "page.html",
            host=host,
            port=port,
            theme=theme,
            open_browser=open_browser,
        )
        source = AdsSource(PLC, ["GVL.*"], interval=1 / rate)
        meter = Meter(monitor, signals, rate)
        monitor.serve(sources=[source, meter], outputs=[web])
    finally:
        plc.terminate()
        plc.communicate(timeout=10)
