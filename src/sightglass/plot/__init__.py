"""100 waveforms at 20 Hz on value-vs-time plots: ``sightglass --demo plot``.

    sightglass --demo plot --open

Each wave is a slow sine, stacked one above the other (wave ``i`` around
``i``), and now and then jumps for a single sample: a spike 50 ms long. The
page (page.html) plots them twice from the dashboard's history: the last
minute at one point per sample, and the last ten minutes at one point per
few samples, where each point still reaches the lowest and highest value it
covers, so every spike shows. The page is plain HTML: a ``data-plot``
element per plot, drawn by sightglass.js.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from sightglass import Monitor, SimulatedSource, WebDashboard

HERE = Path(__file__).parent

WAVES = 100
RATE = 20  # samples a second
SPIKE_EVERY = 20  # seconds, on average, for each wave
HISTORY = 600  # seconds kept: the longer plot's window


class Waves:
    """``generate(seconds)`` for a :class:`SimulatedSource`: every wave's
    value at ``seconds`` into the demo."""

    def __init__(self, seed: int | None = None) -> None:
        self.random = random.Random(seed)
        self.waves = [
            (self.random.uniform(0.03, 0.5), self.random.uniform(0, math.tau))
            for _ in range(WAVES)
        ]

    def __call__(self, seconds: float) -> dict[str, float]:
        chance = 1 / (SPIKE_EVERY * RATE)
        values = {}
        for i, (frequency, phase) in enumerate(self.waves):
            value = i + 0.3 * math.sin(math.tau * frequency * seconds + phase)
            if self.random.random() < chance:
                value += self.random.choice((-1, 1)) * self.random.uniform(1, 3)
            values[f"wave.{i:02d}"] = round(value, 4)
        return values


def build_monitor() -> Monitor:
    return Monitor(history=HISTORY)


def run(
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = False,
    theme: str = "classic",
) -> None:
    """Serve the plots until Ctrl+C."""
    web = WebDashboard(
        HERE / "page.html",
        host=host,
        port=port,
        theme=theme,
        open_browser=open_browser,
    )
    build_monitor().serve(sources=[SimulatedSource(Waves(), rate=RATE)], outputs=[web])
