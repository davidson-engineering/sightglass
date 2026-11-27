"""What the polling sources (REST, InfluxDB) share: big replies arrive whole,
a reply over the limit is refused, and nothing a reply does stops the polling."""

import asyncio
import logging
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from sightglass import InfluxSource, Monitor, RestSource
from sightglass.sources import rest
from sightglass.sources._poll import Feed

from .conftest import until

COUNT = 20_000  # ids: replies of about a MB, which arrive in many network reads


BODIES: dict[str, object] = {}  # made once: building them is not what is tested


def big_json() -> dict:
    if "json" not in BODIES:
        BODIES["json"] = {
            "items": {f"item{i:05d}": {"value": i * 1.5} for i in range(COUNT)}
        }
    return BODIES["json"]


def big_csv() -> str:
    head = (
        "#datatype,string,long,dateTime:RFC3339,double,string,string\r\n"
        ",result,table,_time,_value,_field,_measurement\r\n"
    )
    return "".join(
        head + f",,{i},2026-01-01T00:00:00Z,{i * 1.5},f{i:05d},m\r\n\r\n"
        for i in range(COUNT)
    )


@pytest.fixture
async def server():
    state = {"fail": 0}

    async def rest_reply(request):
        if state["fail"]:
            state["fail"] -= 1
            raise web.HTTPInternalServerError()
        return web.json_response(big_json())

    async def small_reply(request):
        return web.json_response({"items": {"a": {"value": 1}}})

    async def influx_reply(request):
        return web.Response(text=big_csv(), content_type="application/csv")

    app = web.Application()
    app.router.add_get("/big", rest_reply)
    app.router.add_get("/small", small_reply)
    app.router.add_post("/api/v2/query", influx_reply)
    found = TestServer(app)
    await found.start_server()
    found.state = state
    yield found
    await found.close()


async def test_a_big_rest_reply_arrives_whole(server, consume):
    monitor = Monitor(max_elements=COUNT + 10)
    consume(monitor, RestSource(str(server.make_url("/big")), interval=0.2))
    await until(lambda: "items.item19999.value" in monitor, within=20)
    assert len(monitor) == COUNT


async def test_a_big_influxdb_reply_arrives_whole(server, consume):
    monitor = Monitor(max_elements=COUNT + 10)
    source = InfluxSource(
        str(server.make_url("")),
        org="o",
        token="t",
        bucket="b",
        id_from=("_field",),
        interval=0.2,
    )
    consume(monitor, source)
    await until(lambda: "f19999" in monitor, within=20)
    assert len(monitor) == COUNT


async def test_a_reply_over_the_limit_is_refused_not_cut_short(server, consume, caplog):
    source = RestSource(str(server.make_url("/big")), interval=0.2)
    source.max_bytes = 100_000
    monitor = Monitor()
    with caplog.at_level(logging.WARNING):
        consume(monitor, source)
        await until(lambda: any("reply over" in r.getMessage() for r in caplog.records))
    assert len(monitor) == 0


async def test_a_poll_that_fails_unexpectedly_is_a_failed_poll(
    server, consume, caplog, monkeypatch
):
    """A bug, or a document nobody planned for, must not end the polling."""
    real = rest.extract
    calls = []

    def extract(document, endpoint):
        calls.append(1)
        if len(calls) == 1:
            raise RecursionError("maximum recursion depth exceeded")
        return real(document, endpoint)

    monkeypatch.setattr(rest, "extract", extract)
    monitor = Monitor(max_elements=COUNT + 10)
    with caplog.at_level(logging.WARNING):
        consume(monitor, RestSource(str(server.make_url("/big")), interval=0.2))
        await until(lambda: "items.item00000.value" in monitor, within=20)
    assert [r for r in caplog.records if "RecursionError" in r.getMessage()]


async def test_a_failure_is_logged_once_and_again_when_its_cause_changes(caplog):
    from sightglass.sources._poll import Failed, poll

    causes = iter(["HTTP 502", "HTTP 502", "refused", "refused", "refused"])

    async def fetch():
        # Then answering, however many more polls run before the test stops it.
        cause = next(causes, None)
        if cause:
            raise Failed(cause)
        return {}

    with caplog.at_level(logging.INFO):
        task = asyncio.create_task(poll(fetch, 0.01, asyncio.Queue(), "svc"))
        await until(
            lambda: any("answering again" in r.getMessage() for r in caplog.records)
        )
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert [r.getMessage() for r in caplog.records] == [
        "svc: HTTP 502; will keep trying",
        "svc: refused; will keep trying",
        "svc is answering again",
    ]


async def test_a_reply_that_says_it_is_too_big_is_refused_before_it_is_read():
    from types import SimpleNamespace

    from sightglass.sources._poll import Failed, read_body

    def never_read(size):
        raise AssertionError("the body was read")

    response = SimpleNamespace(
        content_length=50_000_000, content=SimpleNamespace(iter_chunked=never_read)
    )
    with pytest.raises(Failed, match="reply over 1000 bytes"):
        await read_body(response, 1000)


async def test_a_big_reply_is_parsed_in_a_worker_thread_and_a_small_one_inline(
    server, consume, monkeypatch
):
    """Parsing a few MB on the event loop stalls every other source and page."""
    import threading

    real = rest.extract
    on_loop: dict[str, bool] = {}  # endpoint path: parsed on the loop's thread

    def extract(document, endpoint):
        path = endpoint.url.rpartition("/")[2]
        on_loop.setdefault(path, threading.current_thread() is threading.main_thread())
        return real(document, endpoint)

    monkeypatch.setattr(rest, "extract", extract)
    monitor = Monitor(max_elements=COUNT + 10)
    consume(monitor, RestSource(str(server.make_url("/big")), interval=0.5))
    await until(lambda: "big" in on_loop, within=20)
    assert on_loop["big"] is False  # about 1 MB: off the loop

    # /big keeps polling meanwhile, so look at /small's own parse.
    consume(monitor, RestSource(str(server.make_url("/small")), interval=0.5))
    await until(lambda: "small" in on_loop, within=10)
    assert on_loop["small"] is True  # a few bytes: not worth a thread


def test_a_feed_reports_polls_a_second_and_how_long_they_took():
    feed = Feed("x")
    assert feed.to_json(0)["rate"] is None  # nothing polled yet
    for i in range(6):  # a poll every 0.2 s, each 30 ms, the third 90 ms
        feed.record(i * 0.2, 0.09 if i == 2 else 0.03, failed=False)
    report = feed.to_json(1.0)
    assert report["rate"] == pytest.approx(5.0)
    assert (report["took"], report["slowest"]) == (0.03, 0.09)
    feed.record(1.2, 0.03, failed=True)
    assert feed.to_json(1.2)["failing"] is True
    assert feed.to_json(60)["rate"] is None  # old polls are forgotten
    assert feed.to_json(60)["slowest"] is None


async def test_a_running_source_shows_how_fast_it_is_polled(server, consume):
    monitor = Monitor()
    consume(monitor, RestSource(str(server.make_url("/small")), interval=0.1))
    await until(lambda: monitor.feeds() and monitor.feeds()[0]["rate"] is not None)
    await asyncio.sleep(1.0)
    (feed,) = monitor.feeds()
    assert feed["name"].endswith("/small") and not feed["failing"]
    assert 7 < feed["rate"] < 11  # asked for 10 a second
    assert 0 < feed["took"] < 0.1


async def test_a_poll_is_timed_finely_where_the_loops_clock_is_coarse(monkeypatch):
    """On Windows before Python 3.13 the event loop's clock ticks every 15.6 ms,
    so a fast poll timed with it took 0 s."""
    from sightglass.sources._poll import poll

    loop = asyncio.get_running_loop()
    real = loop.time
    monkeypatch.setattr(loop, "time", lambda: real() // 0.0156 * 0.0156)

    async def fetch():
        done = time.perf_counter() + 0.002  # a fast reply: well under one tick
        while time.perf_counter() < done:
            pass
        return {}

    feed = Feed("svc")
    task = asyncio.create_task(poll(fetch, 0.05, asyncio.Queue(), "svc", feed))
    await until(lambda: len(feed._polls) >= 3)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert all(took >= 0.002 for _, took in feed._polls)
