"""A dashboard from a config file whose source is a PLC: ADS read from a fake
one, plotted from history. Linux and macOS only, like tests/test_ads.py."""

import json
import struct
import sys
import urllib.request

import pytest

if sys.platform == "win32":
    pytest.skip(
        "ADS tests need pyads' own router (Linux, macOS)", allow_module_level=True
    )

from sightglass.cli import build, parser
from sightglass.fake_plc import FakePlc, FakePlcServer

from .conftest import until

CONFIG = """
title = "Press"

[[source]]
type = "ads"
target = "127.0.0.1.1.1"
variables = ["GVL.*"]
interval = 0.02

[[panel]]
each = ["fPressure"]
title = "{name}"
ids = ["GVL.{name}"]
range = 10
secondary_range = 60
"""


@pytest.fixture
def fake_plc():
    plc = FakePlc()
    plc.server = FakePlcServer(plc)
    plc.server.start()
    yield plc
    plc.server.stop()


async def test_a_plc_feeds_a_time_series_panel_from_a_config_file(fake_plc, tmp_path):
    fake_plc.add("GVL.fPressure", "REAL", struct.pack("<f", 2.5))
    fake_plc.add("GVL.fOther", "REAL", struct.pack("<f", 9.0))
    path = tmp_path / "press.toml"
    path.write_text(CONFIG, "utf-8")
    monitor, sources, outputs = build(
        parser().parse_args(["--config", str(path), "--port", "0"])
    )
    outputs[0].announce = False
    monitor.start(sources=sources, outputs=outputs)
    try:
        await until(lambda: "GVL.fPressure" in monitor, within=10)
        fake_plc.set("GVL.fPressure", struct.pack("<f", 3.5))
        base = outputs[0].url

        def get(route):
            with urllib.request.urlopen(base + route, timeout=5) as response:
                return response.read().decode()

        assert 'data-plot="GVL.fPressure"' in get("")  # the panel
        await until(
            lambda: (
                3.5
                in [
                    point
                    for points in json.loads(
                        get("history?id=GVL.fPressure&seconds=10&points=100")
                    )["signals"]["GVL.fPressure"]
                    for point in ([points] if not isinstance(points, list) else points)
                ]
            ),
            within=10,
        )
        # only what the panel plots is kept as history
        other = json.loads(get("history?id=GVL.fOther&seconds=10&points=100"))
        assert not other["signals"].get("GVL.fOther")
    finally:
        monitor.stop()
