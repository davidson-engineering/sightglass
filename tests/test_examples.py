"""The config examples (examples/*/dashboard.toml) follow one shape, and what
each file asks its panels to show is what its stand-in source makes."""

import fnmatch
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from sightglass.cli import build, parser
from sightglass.dashboard import load

from .conftest import until

EXAMPLES = Path(__file__).parent.parent / "examples"
CONFIGS = sorted(EXAMPLES.glob("*/dashboard.toml"))


def module(path: Path):
    """An example's own module, by path: examples are scripts, not packages."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    found = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(found)
    return found


def shown(dashboard) -> list[str]:
    """Every id pattern a panel shows."""
    return [pattern for panel in dashboard.panels for pattern in panel.ids]


@pytest.mark.parametrize("config", CONFIGS, ids=lambda p: p.parent.name)
def test_a_config_example_has_a_runner_and_a_stand_in_source(config):
    folder = config.parent
    assert (folder / "dashboard.py").exists()
    assert list(folder.glob("fake_*.py")), "a stand-in for the real source"
    dashboard = load(config)  # loads with no environment set
    assert dashboard.sources and dashboard.panels


@pytest.mark.parametrize("config", CONFIGS, ids=lambda p: p.parent.name)
def test_a_config_example_uses_the_dashboards_own_words(config):
    text = config.read_text("utf-8")
    assert "card" not in text.lower()
    assert "[[source]]" in text and "type = " in text


def started(config: Path):
    monitor, sources, outputs = build(
        parser().parse_args(["--config", str(config), "--port", "0"])
    )
    outputs[0].announce = False
    monitor.start(sources=sources, outputs=outputs)
    return monitor


def assert_shows_only_what_exists(dashboard, monitor):
    ids = list(monitor.snapshot())
    for pattern in shown(dashboard):
        assert any(fnmatch.fnmatchcase(i, pattern) for i in ids), pattern


async def test_the_rest_example_shows_what_its_fake_api_makes(monkeypatch):
    folder = EXAMPLES / "rest_grid"
    monkeypatch.syspath_prepend(str(folder))
    monkeypatch.setenv("API_URL", module(folder / "fake_api.py").start_in_thread())
    monitor = started(folder / "dashboard.toml")
    try:
        await until(lambda: "grid.Roof.value.0.0" in monitor, within=10)
        assert_shows_only_what_exists(load(folder / "dashboard.toml"), monitor)
    finally:
        monitor.stop()


async def test_the_influxdb_example_shows_what_its_fake_server_makes(monkeypatch):
    from aiohttp.test_utils import TestServer

    folder = EXAMPLES / "influx_cells"
    fake = module(folder / "fake_influx.py")
    # t = 0 modulo 40: no cell has gone quiet
    monkeypatch.setattr(fake, "time", types.SimpleNamespace(time=lambda: 1_000_000.0))
    server = TestServer(fake.app())
    await server.start_server()
    monkeypatch.setenv("INFLUX_URL", str(server.make_url("")).rstrip("/"))
    monkeypatch.delenv("INFLUX_TOKEN", raising=False)  # the file's demo token
    monitor = started(folder / "dashboard.toml")
    try:
        await until(lambda: "cell.C.ok" in monitor, within=10)
        assert monitor["cell.A.voltage"].value == pytest.approx(
            3.7 + 0.3 * 0.0, abs=0.4
        )
        assert monitor["cell.A.ok"].value is True
        assert_shows_only_what_exists(load(folder / "dashboard.toml"), monitor)
    finally:
        monitor.stop()
        await server.close()


@pytest.mark.skipif(sys.platform == "win32", reason="ADS needs pyads' own router")
async def test_the_ads_example_shows_what_its_fake_plc_makes():
    from sightglass.fake_plc import FakePlc, FakePlcServer

    folder = EXAMPLES / "ads_press"
    fake = module(folder / "fake_plc.py")
    plc = FakePlc()
    fake.program(plc)
    fake.cycle(plc, 1.0)
    server = FakePlcServer(plc)
    server.start()
    monitor = started(folder / "dashboard.toml")
    try:
        await until(lambda: "GVL.bDoorOpen" in monitor, within=10)
        assert_shows_only_what_exists(load(folder / "dashboard.toml"), monitor)
    finally:
        monitor.stop()
        server.stop()
