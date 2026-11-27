"""A stand-in InfluxDB 2.x, for trying the dashboard without a real one.

    uv run --all-extras python examples/influx_cells/fake_influx.py [--port 8086]

POST /api/v2/query (with the token "demo-token") answers with the newest point
of three cells, A, B and C, in Flux's annotated CSV: a voltage, a current and
an `ok` flag each, under the measurement `cell` and the tag `unit`. Every 40
seconds, cell C stops reporting for ten: a series with no recent point, which
the dashboard shows as a gap. It does not read the query. Nothing here needs
sightglass; it is plain aiohttp.
"""

import argparse
import asyncio
import math
import threading
import time
from datetime import UTC, datetime

from aiohttp import web

TOKEN = "demo-token"
UNITS = ("A", "B", "C")
HEADER = (
    "#group,false,false,true,true,false,false,true,true,true\r\n"
    "#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,"
    "{kind},string,string,string\r\n"
    "#default,_result,,,,,,,,\r\n"
    ",result,table,_start,_stop,_time,_value,_field,_measurement,unit\r\n"
)


def points(now: float) -> list[tuple[str, str, str, str]]:
    """(unit, field, datatype, value) for every series that has a point."""
    found = []
    for index, unit in enumerate(UNITS):
        if unit == "C" and now % 40 > 30:
            continue  # cell C has gone quiet
        t = now + index * 3
        found.append((unit, "voltage", "double", f"{3.7 + 0.3 * math.sin(t / 6):.3f}"))
        found.append((unit, "current", "double", f"{2 + math.sin(t / 4):.3f}"))
        found.append((unit, "ok", "boolean", "true" if t % 25 < 20 else "false"))
    return found


def annotated_csv(now: float) -> str:
    stamp = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for table, (unit, field, kind, value) in enumerate(points(now)):
        out.append(HEADER.format(kind=kind))
        out.append(f",,{table},{stamp},{stamp},{stamp},{value},{field},cell,{unit}\r\n")
        out.append("\r\n")
    return "".join(out)


async def query(request: web.Request) -> web.Response:
    if request.headers.get("Authorization") != f"Token {TOKEN}":
        return web.json_response(
            {"code": "unauthorized", "message": "unauthorized access"}, status=401
        )
    return web.Response(text=annotated_csv(time.time()), content_type="application/csv")


def app() -> web.Application:
    application = web.Application()
    application.router.add_post("/api/v2/query", query)
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
        address.append(f"http://127.0.0.1:{runner.addresses[0][1]}")
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    if not ready.wait(10):
        raise RuntimeError("the fake InfluxDB did not start")
    return address[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8086, help="(default: 8086)")
    args = parser.parse_args()
    print(f"Fake InfluxDB: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    web.run_app(app(), host="127.0.0.1", port=args.port, print=None)
