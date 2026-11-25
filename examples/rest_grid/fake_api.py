"""A stand-in REST API, for trying the dashboard without a real one.

    uv run --all-extras python examples/rest_grid/fake_api.py [--port 8000]

GET /api/arrays answers with five sensor arrays, each a grid of readings that
ramps up, holds, drops and recovers, with noise. The grids differ in size, and
the last row of each is not full: its missing cells are null, as a real device
might send them. Nothing here needs sightglass; it is plain aiohttp.
"""

import argparse
import asyncio
import math
import random
import threading
import time
from datetime import UTC, datetime

from aiohttp import web

# name: (rows, columns)
ARRAYS = {
    "North": (13, 14),
    "South": (14, 15),
    "East": (8, 9),
    "West": (8, 8),
    "Roof": (7, 7),
}
CYCLE = 100  # seconds from one ramp to the next


def reading(t: float, shift: float, gain: float) -> float:
    """One cell's value at time ``t``: a ramp, a hold, a drop, a recovery."""
    phase = (t + shift) % CYCLE
    if phase < 60:
        level = 1 - math.exp(-phase / 15)
    else:
        level = (1 - math.exp(-60 / 15)) * math.exp(-(phase - 60) / 5)
    return 4000 * gain * level + random.gauss(0, 4)


def snapshot(now: float) -> dict:
    arrays = {}
    for index, (name, (rows, cols)) in enumerate(ARRAYS.items()):
        shift = index * 17
        gen = random.Random(name)  # the same gains every time
        gains = [[gen.uniform(0.05, 1) for _ in range(cols)] for _ in range(rows)]
        missing = cols // 2  # the last row is not full
        value = [
            [
                None
                if row == rows - 1 and col >= cols - missing
                else round(reading(now, shift, gains[row][col]), 2)
                for col in range(cols)
            ]
            for row in range(rows)
        ]
        flags = [[0 if cell is not None else 8 for cell in line] for line in value]
        arrays[name] = {
            "array_id": name,
            "frame_id": int(now * 5),
            "stale": False,
            "units": "counts",
            "rows": rows,
            "cols": cols,
            "value": value,
            "flags": flags,
        }
    return {
        "timestamp": datetime.fromtimestamp(now, UTC).isoformat(),
        "arrays": arrays,
    }


async def arrays(request: web.Request) -> web.Response:
    return web.json_response(snapshot(time.time()))


def app() -> web.Application:
    application = web.Application()
    application.router.add_get("/api/arrays", arrays)
    return application


def start_in_thread(port: int = 0) -> str:
    """Serve in a background thread until the program exits; return the URL."""
    ready = threading.Event()
    address: list[str] = []

    def run() -> None:
        loop = asyncio.new_event_loop()
        runner = web.AppRunner(app())
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", port)
        loop.run_until_complete(site.start())
        address.append(f"http://127.0.0.1:{runner.addresses[0][1]}/api/arrays")
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    if not ready.wait(10):
        raise RuntimeError("the fake API did not start")
    return address[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8000, help="(default: 8000)")
    args = parser.parse_args()
    print(f"Fake API: http://127.0.0.1:{args.port}/api/arrays  (Ctrl+C to stop)")
    web.run_app(app(), host="127.0.0.1", port=args.port, print=None)
