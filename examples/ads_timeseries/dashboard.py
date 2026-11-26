"""A PLC's variables as time-series panels, described by dashboard.toml.

    uv run --all-extras python examples/ads_timeseries/dashboard.py            # a fake PLC
    PLC_TARGET=5.12.34.56.1.1 sightglass --config examples/ads_timeseries/dashboard.toml

This only starts a fake PLC (sightglass.fake_plc, on 127.0.0.1:48898) and runs
`sightglass --config dashboard.toml` against it; edit the file's target and
variables to read a real one. Needs Linux or macOS: on Windows, pyads needs
TwinCAT's own ADS router. Stop with Ctrl+C.
"""

import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.exit("The fake PLC needs Linux or macOS (pyads needs TwinCAT's router here).")

plc = subprocess.Popen(
    [sys.executable, "-m", "sightglass.load.plc", "3", "10"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    text=True,
)
try:
    if plc.stdout.readline().strip() != "ready":
        sys.exit("The fake PLC didn't start: is port 48898 in use?")
    config = Path(__file__).with_name("dashboard.toml")
    command = [sys.executable, "-m", "sightglass", "--config", str(config)]
    subprocess.call([*command, "--port", "0", "--open"])
finally:
    plc.terminate()
