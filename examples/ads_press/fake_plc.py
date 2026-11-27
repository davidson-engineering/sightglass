"""A stand-in PLC, for trying the dashboard without a real one.

    uv run --all-extras python examples/ads_press/fake_plc.py

A fake TwinCAT PLC (sightglass.fake_plc, on 127.0.0.1:48898, target
127.0.0.1.1.1) running a press: GVL.fPressure and GVL.fTemperature drift,
GVL.nParts counts, GVL.bRunning drops for a few seconds now and then and
GVL.bDoorOpen opens and shuts. Prints "ready" once it answers ADS, and exits
when the program that started it does. Needs Linux or macOS: on Windows,
pyads needs TwinCAT's own ADS router.
"""

import math
import os
import struct
import sys
import time

from sightglass.fake_plc import FakePlc, FakePlcServer

RATE = 10  # PLC cycles a second


def program(plc: FakePlc) -> None:
    """Declare the variables, and return after one cycle's values are written."""
    plc.add("GVL.fPressure", "REAL", bytes(4))
    plc.add("GVL.fTemperature", "REAL", bytes(4))
    plc.add("GVL.nParts", "UDINT", bytes(4))
    plc.add("GVL.bRunning", "BOOL", b"\x01")
    plc.add("GVL.bDoorOpen", "BOOL", b"\x00")


def cycle(plc: FakePlc, t: float) -> None:
    """The variables' values ``t`` seconds in."""
    plc.set("GVL.fPressure", struct.pack("<f", 5 + 3 * math.sin(t / 5)))
    plc.set("GVL.fTemperature", struct.pack("<f", 60 + 8 * math.sin(t / 7)))
    plc.set("GVL.nParts", struct.pack("<I", int(t / 2)))
    plc.set("GVL.bRunning", b"\x00" if t % 40 > 35 else b"\x01")
    plc.set("GVL.bDoorOpen", b"\x01" if t % 20 > 17 else b"\x00")


def main() -> None:
    plc = FakePlc()
    program(plc)
    cycle(plc, 0)
    server = FakePlcServer(plc)
    try:
        server.start()
    except OSError:  # port 48898 taken
        sys.exit("the fake PLC didn't start: is port 48898 in use?")
    print("ready", flush=True)
    parent, started = os.getppid(), time.monotonic()
    try:
        while os.getppid() == parent:  # the dashboard is still there
            cycle(plc, time.monotonic() - started)
            time.sleep(1 / RATE)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()
