"""End-to-end tests: RestSource polling a real aiohttp server."""

import importlib.util
import logging
from pathlib import Path
from time import perf_counter

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from sightglass import Endpoint, Monitor, RestSource
from sightglass.cli import build, parser
from sightglass.sources.rest import _flatten, extract

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
    state = {"up": False, "hits": 0, "headers": None, "live": {}}

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

    async def live(request):
        if state["live"] is None:  # the service is down
            return web.Response(status=503)
        return web.json_response(state["live"])

    async def junk(request):
        return web.Response(text="<html>")

    app = web.Application()
    app.router.add_get("/metrics", metrics)
    app.router.add_get("/health", health)
    app.router.add_get("/flaky", flaky)
    app.router.add_get("/junk", junk)
    app.router.add_get("/live", live)
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


def plotted(monitor, id):
    """The last few points a plot of ``id`` would draw."""
    window = monitor.history.window(perf_counter() - 0.6, perf_counter(), 12)
    return monitor.history.trace(id, window)[-3:]


async def test_null_is_invalid_so_the_value_is_blanked_not_left_showing(api, consume):
    monitor = Monitor(history=60)
    api.state["live"] = {"a": 1.5, "b": 2, "never": None}
    consume(monitor, RestSource(Endpoint(url(api, "/live"), prefix="p"), interval=0.05))
    await until(lambda: "p.a" in monitor and "p.b" in monitor)
    assert "p.never" not in monitor  # never valid: no display at all

    api.state["live"] = {"a": None, "b": 3, "never": None}
    await until(lambda: monitor["p.a"].value == "")  # blank, not 1.5
    await until(lambda: monitor["p.b"].value == 3)  # the others carry on
    await until(lambda: plotted(monitor, "p.a") == [None] * 3)  # a gap, no line
    assert isinstance(plotted(monitor, "p.b")[-1], float | int)
    assert "p.never" not in monitor

    api.state["live"] = {"a": 4, "b": 3}
    await until(lambda: monitor["p.a"].value == 4)  # valid again


async def test_an_id_missing_from_the_reply_is_blanked(api, consume):
    monitor = Monitor()
    api.state["live"] = {"a": 1, "b": 2}
    consume(monitor, RestSource(Endpoint(url(api, "/live"), prefix="p"), interval=0.05))
    await until(lambda: "p.b" in monitor)
    api.state["live"] = {"a": 1}
    await until(lambda: monitor["p.b"].value == "")
    assert monitor["p.a"].value == 1


async def test_an_unreachable_service_blanks_everything_it_showed(api, consume):
    monitor = Monitor(history=60)
    api.state["live"] = {"a": 1.5, "b": {"c": 2}}
    consume(monitor, RestSource(Endpoint(url(api, "/live"), prefix="p"), interval=0.05))
    await until(lambda: "p.b.c" in monitor)

    api.state["live"] = None  # the service goes down
    await until(lambda: monitor["p.a"].value == "" and monitor["p.b.c"].value == "")
    await until(lambda: plotted(monitor, "p.a") == [None] * 3)

    api.state["live"] = {"a": 1.5, "b": {"c": 2}}  # and comes back
    await until(lambda: monitor["p.a"].value == 1.5 and monitor["p.b.c"].value == 2)


def test_extract_names_and_types():
    update = extract(
        {"a": {"b": True, "c": [1, 2]}, "d": [], "e": {}, "f": "text"},
        Endpoint("http://x", prefix="p"),
    )
    assert update == {"p.a.b": True, "p.a.c.0": 1, "p.a.c.1": 2, "p.f": "text"}
    assert extract(5, Endpoint("http://x")) == {}  # a bare value has no id
    assert extract({"a": None, "b": [None, 1]}, Endpoint("http://x")) == {
        "a": None,  # null is invalid data, kept so it can be blanked
        "b.0": None,
        "b.1": 1,
    }


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


async def test_example_fake_api_feeds_the_example_dashboard(consume, monkeypatch):
    """The rest_grid example's config reads the fake API into the ids its panels
    plot, and the null cells of a short last row are skipped."""
    from sightglass.dashboard import load

    fake = load_fake_api()
    server = TestServer(fake.app())
    await server.start_server()
    try:
        monkeypatch.setenv("API_URL", url(server, "/api/arrays"))
        config = load(
            Path(__file__).parent.parent / "examples/rest_grid/dashboard.toml"
        )
        assert [panel.title for panel in config.panels] == list(fake.ARRAYS)
        monitor = Monitor(max_elements=config.max_elements)
        consume(monitor, config.sources[0])
        await until(lambda: "grid.Roof.value.0.0" in monitor)
        rows, cols = fake.ARRAYS["North"]
        assert f"grid.North.value.{rows - 1}.0" in monitor
        assert f"grid.North.value.{rows - 1}.{cols - 1}" not in monitor  # null
    finally:
        await server.close()


def reference_flatten(node, path, key, out):
    """What _flatten did before it was made fast: the specification."""
    from collections.abc import Mapping

    if isinstance(node, Mapping):
        children = ((str(name), value) for name, value in node.items())
    elif isinstance(node, list):
        children = []
        for index, item in enumerate(node):
            name = item.get(key) if isinstance(item, Mapping) else None
            name = (
                str(name) if isinstance(name, str | int) and name != "" else str(index)
            )
            children.append((name, item))
    else:
        if path and (node is None or isinstance(node, str | int | float | bool)):
            out[path] = node
        return
    for name, value in children:
        reference_flatten(value, f"{path}.{name}" if path else name, key, out)


def test_flatten_gives_what_it_always_did_on_any_document():
    import random

    rng = random.Random(7)

    def document(depth=0):
        kind = rng.random()
        if depth > 4 or kind < 0.3:
            return rng.choice([None, True, False, 0, 3, -2.5, "", "text", 1e300])
        if kind < 0.65:
            return {
                rng.choice(["id", "a", "b", "x.y", "7", ""]) + str(i): document(
                    depth + 1
                )
                for i in range(rng.randint(0, 4))
            } | (
                {"id": rng.choice(["n", 3, "", None, True, 1.5])}
                if rng.random() < 0.4
                else {}
            )
        if (
            depth == 0 and rng.random() < 0.3
        ):  # one long list, past the cached positions
            return [rng.choice([1, None, "x", {"id": "n"}]) for _ in range(300)]
        return [document(depth + 1) for _ in range(rng.choice([0, 1, 3]))]

    for _ in range(300):
        doc = document()
        ours, expected = {}, {}
        _flatten(doc, "p", "id", ours)
        reference_flatten(doc, "p", "id", expected)
        assert ours == expected and list(ours) == list(
            expected
        )  # and in the same order


def test_flatten_copes_with_numbered_keys_and_long_lists():
    out = {}
    _flatten({1: "a", "k": [10] * 300}, "", "id", out)
    assert (
        out["1"] == "a" and out["k.0"] == 10 and out["k.299"] == 10 and len(out) == 301
    )
