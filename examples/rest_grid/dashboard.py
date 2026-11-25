"""Readings from a REST API, plotted against time, for a wall display.

    uv run --all-extras python examples/rest_grid/dashboard.py             # a built-in fake API
    uv run --all-extras python examples/rest_grid/dashboard.py URL         # your own

The API answers GET with JSON: {"arrays": {"North": {"value": [[...], ...]}, ...}}.
RestSource polls it 5 times a second and turns it into ids such as
grid.North.value.0.3 (the array, then row, then column). Each array shows lines
for its first row of cells over the last 5 minutes and, bigger, the last
minute. The array names are written into page.html, because the page is plain
HTML: edit them (and `select`/`prefix` below) to match your API. Stop with
Ctrl+C.
"""

import argparse
from pathlib import Path

from fake_api import start_in_thread

from sightglass import Endpoint, Monitor, RestSource, WebDashboard

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("url", nargs="?", help="the API's URL (default: a fake one)")
parser.add_argument("--interval", type=float, default=0.2, help="seconds between polls")
args = parser.parse_args()

url = args.url or start_in_thread()
if not args.url:
    print(f"No URL given: reading the built-in fake API at {url}")

# 5 arrays of ~100-200 cells, each with a flag: about 1,200 ids.
monitor = Monitor(history=360, max_elements=2000)
source = RestSource(
    Endpoint(url, prefix="grid", select="arrays"), interval=args.interval
)
dashboard = WebDashboard(
    Path(__file__).with_name("page.html"),
    title="Sensor grids",
    port=0,
    open_browser=True,
)
monitor.serve(sources=[source], outputs=[dashboard])
