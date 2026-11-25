"""End-to-end tests: RestSource polling a real aiohttp server."""

import importlib.util
import logging
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from sightglass import Endpoint, Monitor, RestSource
from sightglass.cli import build, parser
from sightglass.sources.rest import extract

from .conftest import until

METRICS = {
    "uptime_s": 12.5,
    "process": {"memory_mb": 310.2, "threads": 14},
    "sensors": [
        {"id": "s0", "rate": 30.0, "stale": False},
        {"id": "s1", "rate": 29.5, "stale": True},
        {"rate": 1.0},  # no id: addressed by position
    ],
    "note": None,
}


@pytest.fixture
async def api():
    """A server with a metrics page, a health page that answers 503, and a
    page that is down until ``state["up"]``."""
    state = {"up": False, "hits": 0, "headers": None}

    async def metrics(request):
        state["hits"] += 1
        state["headers"] = dict(request.headers)
        return web.json_response(METRICS)

    async def health(request):
        return web.json_response({"status": "down"}, status=503)

    async def flaky(request):
        if not state["up"]:
            return web.Response(status=500)
        return web.json_response({"data": {"level": 3}})

    async def junk(request):
        return web.Response(text="<html>")

    app = web.Application()
    app.router.add_get("/metrics", metrics)
    app.router.add_get("/health", health)
    app.router.add_get("/flaky", flaky)
    app.router.add_get("/junk", junk)
    server = TestServer(app)
    await server.start_server()
    server.state = state
    yield server
    await server.close()


def url(server, path):
    return str(server.make_url(path))


async def test_polls_json_into_dotted_ids(api, consume):
    monitor = Monitor()
    consume(monitor, RestSource(url(api, "/metrics"), interval=0.05))
    await until(lambda: "sensors.s1.stale" in monitor)
    assert monitor["uptime_s"].value == 12.5
    assert monitor["process.memory_mb"].value == 310.2
    assert monitor["sensors.s0.rate"].value == 30.0
    assert monitor["sensors.2.rate"].value == 1.0  # no id: by position
    assert "note" not in monitor  # null is skipped
    assert api.state["hits"] >= 1
    await until(lambda: api.state["hits"] >= 3)  # and keeps polling


async def test_prefix_select_and_headers(api, consume):
    monitor = Monitor()
    endpoint = Endpoint(url(api, "/metrics"), prefix="svc", select="process")
    consume(
        monitor,
        RestSource(endpoint, interval=0.05, headers={"Authorization": "Bearer x"}),
    )
    await until(lambda: "svc.threads" in monitor)
    assert "svc.memory_mb" in monitor and "uptime_s" not in monitor
    assert api.state["headers"]["Authorization"] == "Bearer x"


async def test_error_statuses_are_ignored_unless_accepted(api, consume, caplog):
    caplog.set_level(logging.INFO)
    monitor = Monitor()
    consume(
        monitor,
        RestSource(
            Endpoint(url(api, "/health"), prefix="health", accept=(503,)),
            Endpoint(url(api, "/flaky"), prefix="flaky", select="data"),
            interval=0.05,
        ),
    )
    await until(lambda: "health.status" in monitor)
    assert monitor["health.status"].value == "down"
    assert "flaky.level" not in monitor
    api.state["up"] = True  # it recovers by itself
    await until(lambda: "flaky.level" in monitor)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "HTTP 500" in warnings[0].getMessage()
    assert any("answering again" in r.getMessage() for r in caplog.records)


async def test_unreachable_and_non_json_replies_do_not_stop_the_source(
    api, consume, caplog
):
    monitor = Monitor()
    dead = TestServer(web.Application())
    await dead.start_server()
    dead_url = url(dead, "/metrics")
    await dead.close()
    consume(
        monitor,
        RestSource(
            dead_url, url(api, "/junk"), Endpoint(url(api, "/metrics")), interval=0.05
        ),
    )
    await until(lambda: "uptime_s" in monitor)  # the good one still gets through

    def logged():
        return " ".join(r.getMessage() for r in caplog.records)

    await until(lambda: dead_url in logged() and "not JSON" in logged(), within=10)


async def test_select_that_is_missing_is_a_failure_not_a_crash(api, consume, caplog):
    monitor = Monitor()
    consume(
        monitor,
        RestSource(Endpoint(url(api, "/metrics"), select="nope"), interval=0.05),
    )
    await until(lambda: any("nope" in r.getMessage() for r in caplog.records))
    assert len(monitor) == 0


def test_extract_names_and_types():
    update = extract(
        {"a": {"b": True, "c": [1, 2]}, "d": [], "e": {}, "f": "text"},
        Endpoint("http://x", prefix="p"),
    )
    assert update == {"p.a.b": True, "p.a.c.0": 1, "p.a.c.1": 2, "p.f": "text"}
    assert extract(5, Endpoint("http://x")) == {}  # a bare value has no id


def test_validation():
    with pytest.raises(ValueError, match="at least one"):
        RestSource()
    with pytest.raises(ValueError, match="interval"):
        RestSource("http://x", interval=0)
    with pytest.raises(ValueError, match="interval"):
        RestSource(Endpoint("http://x", interval=-1))


def test_cli_builds_a_source_with_prefixes_and_headers():
    args = parser().parse_args(
        [
            "--rest", "http://h:8000/api/metrics/",
            "--rest", "http://h:8000/health",
            "--rest-interval", "2",
            "--rest-header", "X-Api-Key: abc:def",
        ]
    )  # fmt: skip
    _, sources, _ = build(args)
    rest = next(s for s in sources if isinstance(s, RestSource))
    assert [e.prefix for e in rest.endpoints] == ["metrics", "health"]
    assert rest.interval == 2 and rest.headers == {"X-Api-Key": "abc:def"}


@pytest.mark.parametrize(
    "argv, message",
    [
        (["--rest", "localhost:8000"], "http"),
        (["--rest", "http://h/x", "--rest-header", "oops"], "NAME: VALUE"),
        (["--rest-interval", "5"], "go with --rest"),
    ],
)
def test_cli_rejects_bad_rest_options(argv, message):
    with pytest.raises(ValueError, match=message):
        build(parser().parse_args(argv))


def load_fake_api():
    path = Path(__file__).parent.parent / "examples" / "rest_grid" / "fake_api.py"
    spec = importlib.util.spec_from_file_location("fake_api", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_example_fake_api_feeds_the_example_dashboard(consume):
    """The rest_grid example's ids are the ones its page plots, and the null
    cells of a short last row are skipped."""
    fake = load_fake_api()
    server = TestServer(fake.app())
    await server.start_server()
    try:
        monitor = Monitor(max_elements=2000)  # ~1,200 ids, as the example sets
        endpoint = Endpoint(url(server, "/api/arrays"), prefix="grid", select="arrays")
        consume(monitor, RestSource(endpoint, interval=0.05))
        await until(lambda: "grid.Roof.value.0.0" in monitor)
        rows, cols = fake.ARRAYS["North"]
        assert f"grid.North.value.{rows - 1}.0" in monitor
        assert f"grid.North.value.{rows - 1}.{cols - 1}" not in monitor  # null
        page = (
            Path(__file__).parent.parent / "examples/rest_grid/page.html"
        ).read_text("utf-8")
        for name in fake.ARRAYS:
            assert f"grid.{name}.value.0.*" in page
    finally:
        await server.close()
