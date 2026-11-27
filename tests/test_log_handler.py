"""LogHandler: a program's log records on its dashboard."""

import logging
import threading

import pytest

from sightglass import Client, LogHandler, LogMonitor, Monitor
from sightglass.elements import LEVEL_STYLES


@pytest.fixture
def logger(request):
    """A logger of each test's own, so records reach only the handler under test."""
    log = logging.getLogger(f"app.{request.node.name}")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    yield log
    for handler in list(log.handlers):
        log.removeHandler(handler)


def test_records_become_lines_of_a_log_it_declares(logger):
    monitor = Monitor()
    logger.addHandler(LogHandler(monitor))
    logger.info("service started with %d workers", 4)
    logger.error("job %d gave up", 7)
    assert isinstance(monitor["log"], LogMonitor)
    lines = monitor["log"].to_json().splitlines()
    assert [line.split(" ", 1)[1] for line in lines] == [
        "INFO service started with 4 workers",
        "ERROR job 7 gave up",
    ]  # each after the time it arrived


def test_a_log_declared_first_is_kept(logger):
    monitor = Monitor()
    monitor.add(LogMonitor("events", lines=2))
    logger.addHandler(LogHandler(monitor, "events", level=logging.WARNING))
    for n in range(3):
        logger.warning("w%d", n)
    logger.info("not shown")
    assert monitor["events"].to_json() == "WARNING w1\nWARNING w2"


def test_a_record_that_cannot_be_formatted_never_raises(monkeypatch):
    monitor = Monitor()
    handler = LogHandler(monitor)
    failed = []
    monkeypatch.setattr(handler, "handleError", failed.append)
    record = logging.LogRecord(
        "app", logging.INFO, __file__, 1, "%d workers", ("four",), None
    )
    handler.handle(record)  # wrong type for %d: logging calls handlers this way
    assert failed == [record] and monitor["log"].to_json() == ""


def test_records_from_many_threads_all_arrive(logger):
    monitor = Monitor()
    monitor.add(LogMonitor("log", lines=400))
    logger.addHandler(LogHandler(monitor))
    monitor.start(outputs=[])
    try:
        threads = [
            threading.Thread(
                target=lambda t=t: [logger.info("t%d %d", t, n) for n in range(100)]
            )
            for t in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        monitor.stop()  # applies everything handed to the loop first
        lines = monitor["log"].to_json().splitlines()
        assert len(lines) == 400
        for t in range(4):  # each thread's lines in the order it logged them
            mine = [int(line.rsplit(" ", 1)[1]) for line in lines if f" t{t} " in line]
            assert mine == list(range(100))
    finally:
        monitor.stop()


def test_a_client_never_sends_its_own_failures_through_itself(logger):
    # Unreachable, and too small for what is logged: the client warns that it
    # drops updates, from inside its own update().
    client = Client("http://127.0.0.1:1", max_pending=2, exit_timeout=0)
    handler = LogHandler(client)
    logger.addHandler(handler)
    logging.getLogger("sightglass.client").addHandler(handler)
    try:
        for n in range(5):
            logger.warning("w%d", n)
        assert [u["log"] for u in client._pending] == ["WARNING w3", "WARNING w4"]
    finally:
        logging.getLogger("sightglass.client").removeHandler(handler)
        client.close(timeout=0)


def test_the_terminal_colours_warnings_and_errors():
    log = LogMonitor("log", lines=3)
    for line in ["INFO fine", "WARNING careful", "ERROR broken"]:
        log.update(line)
    info, warning, error = log.render(40).splitlines()
    assert info == "INFO fine".ljust(40)
    assert warning == LEVEL_STYLES["warning"].apply("WARNING careful".ljust(40))
    assert error == LEVEL_STYLES["error"].apply("ERROR broken".ljust(40))
