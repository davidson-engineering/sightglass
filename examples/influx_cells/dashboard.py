"""Battery cells from InfluxDB on a wall display, described by dashboard.toml.

    uv run --all-extras python examples/influx_cells/dashboard.py             # a built-in fake InfluxDB
    INFLUX_TOKEN=... uv run --all-extras python examples/influx_cells/dashboard.py URL   # yours

This only starts the fake InfluxDB (fake_influx.py) and points dashboard.toml
at it; the dashboard itself is `sightglass --config
examples/influx_cells/dashboard.toml`. For a real server, set INFLUX_URL and
INFLUX_TOKEN and edit the file's org, bucket and measurement. Stop with Ctrl+C.
"""

import os
import subprocess
import sys
from pathlib import Path

from fake_influx import start_in_thread

if len(sys.argv) > 1:
    os.environ["INFLUX_URL"] = sys.argv[1]
else:
    os.environ["INFLUX_URL"] = url = start_in_thread()
    print(f"No URL given: reading the built-in fake InfluxDB at {url}")
config = Path(__file__).with_name("dashboard.toml")
command = [sys.executable, "-m", "sightglass", "--config", str(config)]
sys.exit(subprocess.call([*command, "--port", "0", "--open"]))
