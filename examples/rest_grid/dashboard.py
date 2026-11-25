"""Readings from a REST API on a wall display, described by dashboard.toml.

    uv run --all-extras python examples/rest_grid/dashboard.py             # a built-in fake API
    uv run --all-extras python examples/rest_grid/dashboard.py URL         # your own

This only starts the fake API (fake_api.py) and points dashboard.toml at it;
the dashboard itself is `sightglass --config examples/rest_grid/dashboard.toml`.
Stop with Ctrl+C.
"""

import os
import subprocess
import sys
from pathlib import Path

from fake_api import start_in_thread

if len(sys.argv) > 1:
    url = sys.argv[1]
else:
    url = start_in_thread()
    print(f"No URL given: reading the built-in fake API at {url}")
os.environ["API_URL"] = url  # what dashboard.toml's source reads
config = Path(__file__).with_name("dashboard.toml")
command = [
    sys.executable,
    "-m",
    "sightglass",
    "--config",
    str(config),
    "--port",
    "0",
    "--open",
]
sys.exit(subprocess.call(command))
