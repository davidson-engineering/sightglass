"""``python -m sightglass.load --signals 2000 --rate 50``: the load demo at a
load of your choosing (``sightglass --demo load`` runs 1000 at 20 Hz)."""

import argparse
import sys

from ..cli import error_message
from . import RATE, SIGNALS, run

parser = argparse.ArgumentParser(
    prog="python -m sightglass.load",
    description="Watch the dashboard read a fake PLC over ADS, and what it costs.",
)
parser.add_argument(
    "--signals", type=int, default=SIGNALS, help=f"PLC variables (default {SIGNALS})"
)
parser.add_argument(
    "--rate", type=float, default=RATE, help=f"updates a second (default {RATE})"
)
parser.add_argument("--port", type=int, default=8080, help="(default: 8080)")
parser.add_argument("--open", action="store_true", help="open it in a browser")
args = parser.parse_args()
try:
    run(signals=args.signals, rate=args.rate, port=args.port, open_browser=args.open)
except (ImportError, OSError, ValueError) as error:
    sys.exit(error_message(error, args.port))
