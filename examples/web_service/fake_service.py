"""A stand-in web service, for trying the dashboard without a real one.

    uv run --all-extras python examples/web_service/fake_service.py [--port 8000]

Three endpoints, which behave differently and so are read differently:

  GET /metrics  numbers: counters that only grow, a list of loads, a list of
                workers keyed by their ``id``
  GET /health   answers 503 for a few seconds a minute, with a body saying why
  GET /events   the last 20 events, newest last; a new one every 1.5 s

Nothing here needs sightglass; it is plain aiohttp.
"""

import argparse
import asyncio
import math
import random
import threading
import time
from datetime import UTC, datetime

from aiohttp import web

START = time.time()
EVENT_EVERY = 1.5  # seconds between events
KEEP = 20  # events the service remembers
LEVELS = ["INFO", "INFO", "INFO", "WARNING", "INFO", "ERROR", "INFO"]


def metrics(now: float) -> dict:
    t = now - START
    return {
        "service": {
            "requests_total": int(40 * t),  # counters only grow
            "errors_total": int(t / 3),
            "uptime_s": round(t, 1),
        },
        "load": [  # a list of numbers: ids load.0, load.1, load.2
            round(50 + 40 * math.sin(t / (4 + core)) + random.gauss(0, 2), 1)
            for core in range(3)
        ],
        "workers": [  # objects with an "id": workers.worker-1.rss_mb ...
            {
                "id": f"worker-{n}",
                "rss_mb": round(200 + 30 * n + 20 * math.sin(t / 7 + n), 1),
                "busy_pct": round(50 + 45 * math.sin(t / 5 + 2 * n), 1),
            }
            for n in (1, 2, 3)
        ],
    }


def health(now: float) -> tuple[int, dict]:
    down = (now - START) % 60 > 50  # the last ten seconds of every minute
    checks = {"database": True, "queue": not down}
    body = {"ok": not down, "checks": checks}
    return (503 if down else 200), body


def events(now: float) -> dict:
    newest = int((now - START) / EVENT_EVERY)
    return {
        "events": [
            {
                "id": n,
                "time": datetime.fromtimestamp(START + n * EVENT_EVERY, UTC).strftime(
                    "%H:%M:%S"
                ),
                "level": LEVELS[n % len(LEVELS)],
                "message": f"job {n} {'failed' if n % 7 == 5 else 'finished'}",
            }
            for n in range(max(1, newest - KEEP + 1), newest + 1)
        ]
    }


async def get_metrics(request: web.Request) -> web.Response:
    return web.json_response(metrics(time.time()))


async def get_health(request: web.Request) -> web.Response:
    status, body = health(time.time())
    return web.json_response(body, status=status)


async def get_events(request: web.Request) -> web.Response:
    return web.json_response(events(time.time()))


def app() -> web.Application:
    application = web.Application()
    application.router.add_get("/metrics", get_metrics)
    application.router.add_get("/health", get_health)
    application.router.add_get("/events", get_events)
    return application


def start_in_thread(port: int = 0) -> str:
    """Serve in a background thread until the program exits; return the base
    URL (``http://127.0.0.1:PORT``)."""
    ready = threading.Event()
    address: list[str] = []

    def run() -> None:
        loop = asyncio.new_event_loop()
        runner = web.AppRunner(app())
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", port)
        loop.run_until_complete(site.start())
        address.append(f"http://127.0.0.1:{runner.addresses[0][1]}")
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    if not ready.wait(10):
        raise RuntimeError("the fake service did not start")
    return address[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8000, help="(default: 8000)")
    args = parser.parse_args()
    print(f"Fake service: http://127.0.0.1:{args.port}/metrics  (Ctrl+C to stop)")
    web.run_app(app(), host="127.0.0.1", port=args.port, print=None)
