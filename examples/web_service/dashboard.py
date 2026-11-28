"""A web service's metrics, health check and event feed, from dashboard.toml.

    uv run --all-extras python examples/web_service/dashboard.py          # a built-in fake service
    uv run --all-extras python examples/web_service/dashboard.py URL      # your own, e.g. http://host:8000

This only starts the fake service (fake_service.py) and points dashboard.toml
at it; the dashboard itself is `sightglass --config
examples/web_service/dashboard.toml`. Stop with Ctrl+C.
"""

import os
import subprocess
import sys
from pathlib import Path

from fake_service import start_in_thread

if len(sys.argv) > 1:
    url = sys.argv[1].rstrip("/")
else:
    url = start_in_thread()
    print(f"No URL given: reading the built-in fake service at {url}")
os.environ["SERVICE_URL"] = url  # what dashboard.toml's sources read
config = Path(__file__).with_name("dashboard.toml")
command = [sys.executable, "-m", "sightglass", "--config", str(config)]
sys.exit(subprocess.call([*command, "--port", "0", "--open"]))
