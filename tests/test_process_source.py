"""ProcessSource: the running program's own CPU, memory, threads and uptime."""

import asyncio
import sys
import threading
import time

import psutil
import pytest

from sightglass import Monitor, ProcessSource, Sparkline, TextElement
from sightglass.sources import process

from .conftest import until

# psutil's own answers, read before a test hides psutil (it looks itself up in
# sys.modules, so calls made while it is hidden fail on Linux).
STARTED = psutil.Process().create_time()
CORES = psutil.cpu_count() or 1


@pytest.fixture(params=["psutil", "standard library"])
def kind(request, monkeypatch):
    """Each test runs with psutil, and as if it weren't installed."""
    if request.param == "standard library":
        monkeypatch.setitem(sys.modules, "psutil", None)  # import raises
    return request.param


async def first_values(monitor, ids):
    await until(lambda: all(id in monitor.signals for id in ids))
    return {id: monitor.signals[id].value for id in ids}  # as sent


async def test_a_program_shows_its_cpu_memory_threads_and_uptime(kind):
    monitor = Monitor()
    task = asyncio.create_task(monitor.run(sources=[ProcessSource(interval=0.05)]))
    try:
        values = await first_values(
            monitor,
            ["process.cpu", "process.rss", "process.threads", "process.uptime"],
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert 0 <= values["process.cpu"] <= 100 * CORES
    assert values["process.rss"] > 10 * 2**20  # this test runner: well over 10 MB
    assert values["process.threads"] == threading.active_count()
    assert values["process.uptime"] >= 0
    # Declared, so they read as a chart in MB and a percentage, not raw bytes.
    rss = monitor["process.rss"]
    assert isinstance(rss, Sparkline)
    assert rss.label == "Memory" and rss.units == "MB"
    assert rss.to_json()["text"].endswith(" MB")
    assert monitor["process.cpu"].units == "%"


def test_cpu_is_left_out_of_the_first_sample_not_sent_as_zero(kind):
    sampler = process.Sampler()
    assert "cpu" not in sampler.sample()  # nothing to measure it over yet
    assert "cpu" in sampler.sample()


def test_cpu_is_the_share_of_one_core_used_since_the_last_sample(kind):
    sampler = process.Sampler()
    sampler.sample()
    busy = time.process_time() + 0.2
    while time.process_time() < busy:
        pass
    # 0.2 s of CPU, over a little more than 0.2 s of wall time.
    assert 60 < sampler.sample()["cpu"] <= 100 * CORES


def test_memory_without_psutil_matches_psutils(monkeypatch):
    """The standard library route (/proc, task_info, GetProcessMemoryInfo)
    reads the same resident memory psutil does."""
    expected = psutil.Process().memory_info().rss
    monkeypatch.setitem(sys.modules, "psutil", None)
    rss = process.Sampler().sample()["rss"]
    assert abs(rss - expected) / expected < 0.1


def test_uptime_is_since_the_process_started(kind):
    started = STARTED
    uptime = process.Sampler().sample()["uptime"]
    if kind == "standard library" and not sys.platform.startswith("linux"):
        # Elsewhere, without psutil: since sightglass was imported.
        assert 0 <= uptime <= time.time() - started + 1
    else:
        assert uptime == pytest.approx(time.time() - started, abs=1)


def test_open_files_are_counted_where_the_platform_tells(kind):
    sample = process.Sampler().sample()
    if sys.platform == "win32":
        assert "fds" not in sample  # handles, not files
        assert all(element.id != "process.fds" for element in ProcessSource().elements)
        return
    before = sample["fds"]
    with open(__file__), open(__file__):
        assert process.Sampler().sample()["fds"] == before + 2


def test_garbage_collections_are_counted(kind):
    import gc

    before = process.Sampler().sample()["gc_collections"]
    gc.collect()
    assert process.Sampler().sample()["gc_collections"] > before


async def test_elements_declared_by_the_program_are_kept(kind):
    monitor = Monitor(strict=True)  # the source's own ids are declared, not unknown
    monitor.add(TextElement("app.rss", label="RSS"))
    task = asyncio.create_task(
        monitor.run(sources=[ProcessSource(interval=0.05, prefix="app")])
    )
    try:
        values = await first_values(monitor, ["app.rss", "app.cpu", "app.threads"])
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert type(monitor["app.rss"]) is TextElement
    assert monitor["app.rss"].label == "RSS"
    assert isinstance(monitor["app.cpu"], Sparkline)
    assert monitor.rejected == 0
    assert values["app.threads"] >= 1


def test_interval_must_be_positive():
    with pytest.raises(ValueError, match="interval"):
        ProcessSource(interval=0)
