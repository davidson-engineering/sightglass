"""The load demo's PLC: a fake TwinCAT PLC (:mod:`sightglass.fake_plc`) whose
``GVL.fSignal0000`` ... are updated ``rate`` times a second.

    python -m sightglass.load.plc SIGNALS RATE

Each signal is a slow wave with a little noise, around its own level. 10 to
20 seconds in, and then every 40 to 80 seconds, one of them, chosen at
random, jumps for two updates (two
PLC cycles, so a dashboard reading once a cycle can't miss it); then
``GVL.nGlitches`` counts it and ``GVL.nGlitchSignal`` says which. ``GVL.fClock``
is the wall-clock time of the latest update, so the dashboard can measure
how long a value takes to reach it. Prints "ready" once it answers ADS, and
exits when the process that started it does.
"""

from __future__ import annotations

import math
import os
import random
import struct
import sys
import time

from sightglass.fake_plc import FakePlc, FakePlcServer

GLITCH_FIRST = (10, 20)  # seconds from the start to the first glitch
GLITCH_EVERY = (40, 80)  # seconds between glitches after that, at random
GLITCH_UPDATES = 2


class Program:
    """The PLC's program: every signal's next value, and the glitches."""

    def __init__(self, plc: FakePlc, signals: int) -> None:
        self.plc = plc
        self.signals = signals
        # The signals first, 8 bytes apart (REALs, aligned as TwinCAT lays
        # them out), so one struct call writes them all.
        for i in range(signals):
            plc.add(f"GVL.fSignal{i:04d}", "REAL", bytes(4))
        self.layout = struct.Struct("<" + "f4x" * signals)
        plc.add("GVL.fClock", "LREAL", bytes(8))
        plc.add("GVL.nCycle", "UDINT", bytes(4))
        plc.add("GVL.nGlitches", "UDINT", bytes(4))
        plc.add("GVL.nGlitchSignal", "DINT", struct.pack("<i", -1))
        self.random = rng = random.Random()
        self.waves = [
            (
                rng.uniform(-50, 50),  # level
                rng.uniform(1, 10),  # amplitude
                rng.uniform(0.01, 0.3),  # Hz
                rng.uniform(0, math.tau),  # phase
            )
            for _ in range(signals)
        ]
        self.started = time.monotonic()
        self.next_glitch = self.started + rng.uniform(*GLITCH_FIRST)
        self.glitch, self.glitch_left, self.glitches = -1, 0, 0
        self.cycle = 0

    def update(self) -> None:
        """One PLC cycle: every signal's value now, written to memory."""
        rng, now = self.random, time.monotonic()
        t = now - self.started
        values = [
            level
            + amplitude * math.sin(math.tau * hz * t + phase)
            + rng.gauss(0, amplitude * 0.02)
            for level, amplitude, hz, phase in self.waves
        ]
        if self.glitch_left == 0 and now >= self.next_glitch:
            self.glitch = rng.randrange(self.signals)
            self.glitch_left = GLITCH_UPDATES
            self.next_glitch = now + rng.uniform(*GLITCH_EVERY)
            self.glitches += 1
        if self.glitch_left:
            level, amplitude, _, _ = self.waves[self.glitch]
            values[self.glitch] = level + amplitude * rng.choice((-6, 6))
            self.glitch_left -= 1
        self.cycle += 1
        plc = self.plc
        with plc._lock:
            self.layout.pack_into(plc.memory, 0, *values)
            for name, form, value in (
                ("GVL.fClock", "<d", time.time()),
                ("GVL.nCycle", "<I", self.cycle & 0xFFFFFFFF),
                ("GVL.nGlitches", "<I", self.glitches),
                ("GVL.nGlitchSignal", "<i", self.glitch),
            ):
                offset = plc.symbols[name.upper()].offset
                struct.pack_into(form, plc.memory, offset, value)


def main(signals: int, rate: float) -> None:
    plc = FakePlc()
    program = Program(plc, signals)
    program.update()  # values from the first read on, not zeros
    server = FakePlcServer(plc)
    server.start()
    print("ready", flush=True)
    parent = os.getppid()
    try:
        while os.getppid() == parent:  # the dashboard is still there
            program.update()
            due = program.started + program.cycle / rate
            time.sleep(max(due - time.monotonic(), 0))
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main(int(sys.argv[1]), float(sys.argv[2]))
