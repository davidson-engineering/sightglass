"""The config examples (examples/*/dashboard.toml) follow one shape, and what
each file asks its panels to show is what its stand-in source makes."""

import fnmatch
import importlib.util
import types
from pathlib import Path

import pytest

from sightglass.cli import build, parser
from sightglass.dashboard import load

from .conftest import needs_twincat_router, until

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


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(c, marks=needs_twincat_router)
        if c.parent.name == "ads_press"
        else c
        for c in CONFIGS
    ],
    ids=lambda p: p.parent.name,
)
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


@needs_twincat_router
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


async def test_the_web_service_example_shows_what_its_fake_service_makes(monkeypatch):
    folder = EXAMPLES / "web_service"
    monkeypatch.syspath_prepend(str(folder))
    fake = module(folder / "fake_service.py")
    monkeypatch.setenv("SERVICE_URL", fake.start_in_thread())
    monitor = started(folder / "dashboard.toml")
    try:
        await until(
            lambda: "health.ok" in monitor and "svc.load.0" in monitor, within=10
        )
        dashboard = load(folder / "dashboard.toml")
        assert_shows_only_what_exists(dashboard, monitor)
        assert "svc.workers.worker-1.busy_pct" in monitor
        assert "svc.workers.worker-1.id" not in monitor
        await until(
            lambda: monitor["events"].entries, within=10
        )  # one log, no ids per event
    finally:
        monitor.stop()


def test_the_fake_service_health_check_answers_503_when_down():
    fake = module(EXAMPLES / "web_service" / "fake_service.py")
    status, body = fake.health(fake.START + 55)
    assert status == 503 and body["ok"] is False
    assert fake.health(fake.START + 5) == (
        200,
        {"ok": True, "checks": {"database": True, "queue": True}},
    )
