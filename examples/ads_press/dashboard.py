"""A press line's PLC on a wall display, described by dashboard.toml.

    uv run --all-extras python examples/ads_press/dashboard.py              # a fake PLC
    PLC_TARGET=5.12.34.56.1.1 sightglass --config examples/ads_press/dashboard.toml

This only starts the fake PLC (fake_plc.py) and runs `sightglass --config
dashboard.toml` against it; the dashboard itself is the file. Edit its target
and variables to read a real PLC. Needs Linux or macOS: on Windows, pyads needs
TwinCAT's own ADS router. Stop with Ctrl+C.
"""

import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.exit("The fake PLC needs Linux or macOS (pyads needs TwinCAT's router here).")

here = Path(__file__).parent
plc = subprocess.Popen(
    [sys.executable, str(here / "fake_plc.py")],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    text=True,
)
try:
    if plc.stdout.readline().strip() != "ready":
        sys.exit("The fake PLC didn't start: is port 48898 in use?")
    command = [
        sys.executable,
        "-m",
        "sightglass",
        "--config",
        str(here / "dashboard.toml"),
    ]
    subprocess.call([*command, "--port", "0", "--open"])
finally:
    plc.terminate()
