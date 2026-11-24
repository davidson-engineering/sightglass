"""History: every numeric signal's last minutes, for value-vs-time plots."""

import asyncio
import math
import time
from time import monotonic

import pytest
from aiohttp.test_utils import TestClient, TestServer

from sightglass import Monitor, WebDashboard
from sightglass.history import History

RES = 0.05
T = 1000.0  # a monotonic() time at the start of a slot


def at(slot: int) -> float:
    """A time inside the ``slot``-th slot after ``T``'s."""
    return T + (slot + 0.5) * RES


def trace(history: History, id: str, first: int, last: int) -> list:
    """Slots ``first`` to ``last`` (relative to ``T``), one point each."""
    result = history.query([id], at(first), at(last), points=last - first + 1)
    return result["signals"][id]


def test_each_slot_holds_the_value_and_a_steady_value_carries_on():
    history = History(seconds=1, resolution=RES)
    history.record([("speed", 2.5)], at(0))
    history.record([("speed", 3)], at(2))
    assert trace(history, "speed", -1, 4) == [None, 2.5, 2.5, [2.5, 3], 3, 3]


def test_a_slot_spans_every_value_it_saw():
    history = History(seconds=1, resolution=RES)
    history.record([("t", 5), ("t", 9), ("t", 7)], at(0))
    history.record([("t", 1)], at(1))  # from 7, held, down to 1
    assert trace(history, "t", 0, 2) == [[5, 9], [1, 7], 1]


def test_a_value_that_is_not_a_number_leaves_a_gap():
    history = History(seconds=1, resolution=RES)
    history.record([("v", 3)], at(0))
    history.record([("v", "n/a")], at(1))
    history.record([("v", "4")], at(3))  # numeric text counts
    history.record([("v", True)], at(4))  # and true/false, as 1/0
    # Each slot starts from the value before it: slot 4 goes from 4 to 1.
    assert trace(history, "v", 0, 4) == [3, 3, None, 4, [1, 4]]


def test_an_id_that_never_sent_a_number_has_no_history():
    history = History(seconds=1, resolution=RES)
    history.record([("status", "running"), ("speed", 1)], at(0))
    assert "status" not in history
    assert "speed" in history
    result = history.query(["status", "unknown"], at(0), at(1), points=10)
    assert result["signals"] == {"status": None, "unknown": None}


def test_values_older_than_the_history_are_forgotten():
    history = History(seconds=1, resolution=RES)  # 20 slots
    history.record([("x", 1)], at(0))
    history.record([("x", 2)], at(25))
    points = trace(history, "x", 0, 25)
    assert points[:6] == [None] * 6  # slots 0 to 5 have gone
    assert points[6:25] == [1] * 19  # held until the 2 arrived
    assert points[25] == [1, 2]


def test_a_long_window_is_reduced_to_the_lowest_and_highest_per_point():
    history = History(seconds=10, resolution=RES)  # 200 slots
    for slot in range(200):
        history.record([("wave", slot % 10)], at(slot))
    result = history.query(["wave"], at(0), at(199), points=20)
    assert result["points"] == len(result["signals"]["wave"]) <= 20
    step_slots = round(result["step"] / RES)
    assert step_slots >= 10
    # Every point covers a whole period of the wave, except the first's
    # leading slots: low 0 and high 9 (the held 9 starts each slot's period).
    assert all(point == [0, 9] for point in result["signals"]["wave"][1:-1])


def test_points_line_up_on_multiples_of_their_step():
    """A point covers the same slots whatever window asked for it, so a plot
    can append new points to the ones it has."""
    history = History(seconds=10, resolution=RES)
    for slot in range(200):
        history.record([("n", slot)], at(slot))
    a = history.query(["n"], at(0), at(150), points=10)
    b = history.query(["n"], at(10), at(160), points=10)  # as long
    assert a["step"] == b["step"]
    step = a["step"]
    for result in (a, b):
        assert (result["start"] / step) == pytest.approx(round(result["start"] / step))
    offset = round((b["start"] - a["start"]) / step)
    overlap = a["signals"]["n"][offset:-1]  # a's last point is still filling
    assert overlap == b["signals"]["n"][: len(overlap)]


def test_the_newest_point_ends_at_the_end_of_the_window():
    """Not later: a held value isn't claimed for slots that haven't happened."""
    history = History(seconds=10, resolution=RES)
    history.record([("x", 1)], at(0))
    result = history.query(["x"], at(0), at(3), points=100)
    assert result["points"] == 4
    assert result["start"] == pytest.approx(T)
    assert result["step"] == pytest.approx(RES)


def test_points_are_rounded_to_what_float32_holds():
    history = History(seconds=1, resolution=RES)
    history.record([("x", 0.1), ("y", 123456.789)], at(0))
    result = history.query(["x", "y"], at(0), at(0), points=2)
    assert result["signals"] == {"x": [0.1], "y": [123456.8]}


def test_query_checks_its_window():
    history = History(seconds=1, resolution=RES)
    with pytest.raises(ValueError, match="points"):
        history.query(["x"], at(0), at(1), points=1)
    with pytest.raises(ValueError, match="end"):
        history.query(["x"], at(2), at(1), points=10)


def test_history_size_is_checked():
    with pytest.raises(ValueError, match="seconds"):
        History(seconds=0)
    with pytest.raises(ValueError, match="resolution"):
        History(seconds=1, resolution=0)
    assert History(seconds=600).slots == 12000


# -- in a monitor -------------------------------------------------------------


def test_a_monitor_keeps_history_only_when_asked():
    assert Monitor().history is None
    monitor = Monitor(history=60)
    monitor.update({"speed": 4, "job.rate": 2})
    assert monitor.history.seconds == 60
    now = monotonic()
    result = monitor.history.query(["speed", "job.rate"], now - 1, now, points=2)
    assert result["signals"]["speed"][-1] == 4
    assert result["signals"]["job.rate"][-1] == 2


def test_a_monitor_records_values_as_sent_not_as_shown():
    from sightglass import ProgressBar

    monitor = Monitor(history=60)
    monitor.add(ProgressBar("job", total=50))
    monitor.update({"job": 25})  # shown as 50%
    now = monotonic()
    assert monitor.history.query(["job"], now - 1, now, 2)["signals"]["job"][-1] == 25


# -- over HTTP ----------------------------------------------------------------


@pytest.fixture
async def client():
    monitor = Monitor(history=60)
    async with TestClient(TestServer(WebDashboard().app(monitor))) as client:
        client.monitor = monitor
        yield client


async def test_get_history_returns_points_in_wall_clock_time(client):
    client.monitor.update({"speed": 3, "X.velocity": 12})
    before = time.time()
    response = await client.get(
        "/history", params=[("id", "speed"), ("id", "X.velocity"), ("seconds", "2")]
    )
    assert response.status == 200
    result = await response.json()
    assert result["signals"]["speed"][-1] == 3
    assert result["signals"]["X.velocity"][-1] == 12
    assert result["points"] == len(result["signals"]["speed"]) <= 600
    end = result["start"] + result["points"] * result["step"]
    assert before - 2.1 <= result["start"] <= before - 1.9
    assert before <= end <= time.time() + result["step"]


async def test_get_history_takes_a_window_in_unix_seconds(client):
    client.monitor.update({"speed": 3})
    now = time.time()
    response = await client.get(
        "/history",
        params={"id": "speed", "from": now - 10, "to": now, "points": "5"},
    )
    result = await response.json()
    assert result["points"] <= 5
    assert result["signals"]["speed"][-1] == 3
    assert result["start"] <= now - 10 < result["start"] + result["step"]


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({}, "id"),
        ({"id": "x", "points": "many"}, "points"),
        ({"id": "x", "points": "1"}, "points"),
        ({"id": "x", "points": "100000"}, "points"),
        ({"id": "x", "from": "10", "to": "5"}, "from"),
        ({"id": "x", "seconds": "-1"}, "seconds"),
        ({"id": "x", "seconds": "nan"}, "seconds"),
    ],
)
async def test_get_history_says_what_is_wrong(client, params, error):
    response = await client.get("/history", params=params)
    assert response.status == 400
    assert error in await response.text()


async def test_get_history_is_not_found_while_history_is_off():
    async with TestClient(TestServer(WebDashboard().app(Monitor()))) as client:
        response = await client.get("/history", params={"id": "x"})
        assert response.status == 404
        assert "--history" in await response.text()


def test_points_never_hold_nan_or_infinity():
    history = History(seconds=1, resolution=RES)
    history.record([("x", math.inf), ("x", 1), ("x", math.nan)], at(0))
    assert trace(history, "x", 0, 1) == [1, None]


def test_a_number_too_big_for_float32_is_plotted_at_its_largest():
    history = History(seconds=1, resolution=RES)
    history.record([("x", 1e39), ("y", -1e39)], at(0))
    result = history.query(["x", "y"], at(0), at(1), points=2)
    assert result["signals"] == {"x": [3.402823e38] * 2, "y": [-3.402823e38] * 2}


def test_a_point_with_a_gap_spans_the_numbers_around_it():
    history = History(seconds=10, resolution=RES)
    history.record([("x", 2)], at(0))
    history.record([("x", "off-line")], at(3))
    history.record([("x", 8)], at(6))
    result = history.query(["x"], at(0), at(9), points=2)  # 10 slots a point
    assert result["signals"]["x"] == [[2, 8]] * result["points"]


async def test_a_long_query_lets_values_keep_arriving(client, monkeypatch):
    """Ids are reduced a few milliseconds' worth at a time, handing the event
    loop back in between, so a big request doesn't hold up incoming values
    (or other pages)."""
    history = client.monitor.history
    events = []
    trace = history.trace

    def traced(id, window):
        events.append("trace")
        time.sleep(0.003)  # a slow id: more than a slice of the loop
        return trace(id, window)

    monkeypatch.setattr(history, "trace", traced)

    async def other_work():
        while True:
            events.append("other")
            await asyncio.sleep(0)

    task = asyncio.create_task(other_work())
    try:
        response = await client.get("/history", params=[("id", "a"), ("id", "b")])
        assert response.status == 200
    finally:
        task.cancel()
    first, second = (i for i, event in enumerate(events) if event == "trace")
    assert "other" in events[first:second]


async def test_get_history_expands_a_pattern_to_the_ids_it_matches(client):
    """``*`` stands for any characters, so a page can plot a whole group."""
    client.monitor.update({"wave.1": 1, "wave.2": 2, "speed": 3, "wave.10": 10})
    response = await client.get("/history", params=[("id", "wave.*"), ("id", "x*")])
    result = await response.json()
    assert list(result["signals"]) == ["wave.1", "wave.2", "wave.10"]  # as first sent


async def test_get_history_takes_a_step_so_a_plot_can_fetch_just_new_points(client):
    """A plot asks once for its window, then again from its newest point at
    the same step: the points line up, and the first one is redone."""
    client.monitor.update({"speed": 3})
    whole = await (
        await client.get("/history", params={"id": "speed", "seconds": "6"})
    ).json()
    newest = whole["start"] + (whole["points"] - 1) * whole["step"]
    client.monitor.update({"speed": 9})
    params = {"id": "speed", "from": str(newest), "step": str(whole["step"])}
    new = await (await client.get("/history", params=params)).json()
    assert new["step"] == whole["step"]
    offset = (new["start"] - whole["start"]) / whole["step"]
    # Within microseconds: the wall clock and monotonic() drift apart a little.
    assert offset == pytest.approx(round(offset), abs=1e-3)
    assert new["signals"]["speed"][-1] in (9, [3, 9])


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"id": "x", "step": "0"}, "step"),
        ({"id": "x", "step": "fast"}, "step"),
        ({"id": "x", "step": "1", "points": "10"}, "step or points"),
        ({"id": "x", "step": "0.05", "seconds": "600"}, "points"),  # 12001 points
    ],
)
async def test_get_history_says_what_is_wrong_with_a_step(client, params, error):
    response = await client.get("/history", params=params)
    assert response.status == 400
    assert error in await response.text()


async def test_get_history_gives_fewer_points_to_many_ids(client, monkeypatch):
    """An answer holds at most HISTORY_POINT_BUDGET points in all: more ids,
    fewer points each, so a heat map of thousands of ids stays a size a
    page can load."""
    monkeypatch.setattr("sightglass.web.HISTORY_POINT_BUDGET", 300)
    client.monitor.update({f"wave.{i}": i for i in range(30)})
    response = await client.get(
        "/history", params={"id": "wave.*", "seconds": "60", "points": "600"}
    )
    result = await response.json()
    assert len(result["signals"]) == 30
    assert result["points"] <= 300 // 30
    assert all(len(points) == result["points"] for points in result["signals"].values())


async def test_get_history_says_when_a_pattern_matches_too_many_ids(
    client, monkeypatch
):
    monkeypatch.setattr("sightglass.web.HISTORY_MAX_IDS", 3)
    client.monitor.update({f"wave.{i}": i for i in range(5)})
    response = await client.get("/history", params={"id": "wave.*"})
    assert response.status == 400
    assert "at most 3 ids per request; these match 5" in await response.text()
