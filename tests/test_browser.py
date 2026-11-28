"""The browser client (sightglass.js) in a real browser: pages stay bound to
live values exactly as documented.

Needs Chromium once: ``uv run playwright install chromium``. Uses Playwright's
async API, so these run alongside the other asyncio tests in one session.
"""

import asyncio
import os
import re
import subprocess
import sys

import pytest
from playwright.async_api import async_playwright, expect

from sightglass import (
    Coordinate,
    Counters,
    IndicatorLamp,
    LogMonitor,
    Monitor,
    ProcessSource,
    ProgressBar,
    RangeBar,
    Sparkline,
    TextElement,
    TextFormat,
    WebDashboard,
)
from sightglass.sources._poll import Feed

from .conftest import until

pytestmark = pytest.mark.browser


@pytest.fixture
async def page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            yield await browser.new_page()
        finally:
            await browser.close()


@pytest.fixture
def serve(tmp_path):
    """``serve(body, monitor)``: start a dashboard whose page is ``body`` (the
    generic page for None) and return its address."""
    started = []

    def serve(body: str | None, monitor: Monitor, **options) -> str:
        page = None
        if body is not None:
            page = tmp_path / "page.html"
            page.write_text(
                f"<!doctype html><html><head>{options.pop('head', '')}"
                f"<style>{options.pop('css', '')}</style></head><body>{body}"
                '<script src="/_sightglass/sightglass.js"></script></body></html>'
            )
        sources = options.pop("sources", ())
        web = WebDashboard(page, port=0, announce=False, **options)
        started.append(monitor.start(sources=sources, outputs=[web]))
        return web.url

    yield serve
    for monitor in started:
        monitor.stop()


async def test_text_width_and_state_bindings_follow_the_values(page, serve):
    monitor = Monitor()
    monitor.add(
        RangeBar("speed", max=200, units="km/h", format=TextFormat(precision=0)),
        IndicatorLamp("pump"),
    )
    await page.goto(
        serve(
            '<span id="speed" data-bind="speed.text"></span>'
            '<div id="bar" data-bind="speed.ratio" data-mode="width"></div>'
            '<div id="pump" data-bind="pump" data-mode="state"></div>'
            '<span id="created" data-bind="job.status"></span>',
            monitor,
        )
    )
    await expect(page.locator("html")).to_have_attribute("data-connection", "open")
    await expect(page.locator("#pump")).to_have_attribute("data-state", "off")

    monitor.update({"speed": 50, "pump": True, "job.status": "running"})
    await expect(page.locator("#speed")).to_have_text("50 km/h")
    await expect(page.locator("#bar")).to_have_attribute("style", "width: 25%;")
    await expect(page.locator("#pump")).to_have_attribute("data-state", "on")
    await expect(page.locator("#created")).to_have_text("running")  # made on first use


async def test_state_binding_reads_text_from_devices(page, serve):
    monitor = Monitor()
    body = '<i id="pump" data-bind="pump" data-mode="state"></i>'
    await page.goto(serve(body, monitor))
    lamp = page.locator("#pump")
    for value, state in [("on", "on"), ("off", "off"), ("1", "on"), (" No ", "off")]:
        monitor.set("pump", value)
        await expect(lamp).to_have_attribute("data-state", state)


async def test_sparkline_binding_draws_the_history(page, serve):
    monitor = Monitor()
    monitor.add(Sparkline("rate", points=5))
    chart = '<div id="chart" data-bind="rate.values" data-mode="sparkline"></div>'
    await page.goto(serve(chart, monitor))
    monitor.update({"rate": 1}, {"rate": 3}, {"rate": 2})
    await expect(page.locator("#chart polyline")).to_have_attribute(
        "points", "0.00,19.00 50.00,1.00 100.00,10.00"
    )
    monitor.update(*[{"rate": 7}] * 5)  # steady: a level line, not one at zero
    await expect(page.locator("#chart polyline")).to_have_attribute(
        "points", "0.00,10.00 25.00,10.00 50.00,10.00 75.00,10.00 100.00,10.00"
    )


async def test_var_binding_hands_numbers_to_css(page, serve):
    monitor = Monitor()
    monitor.add(RangeBar("q", max=40), TextElement("pitch"), IndicatorLamp("lit"))
    await page.goto(
        serve(
            '<div id="q" data-bind="q.ratio" data-mode="var"></div>'
            '<div id="pitch" data-bind="pitch" data-mode="var"></div>'
            '<div id="lit" data-bind="lit" data-mode="var"></div>',
            monitor,
            css="#q { width: calc(var(--value) * 200px) }"
            "#pitch { rotate: calc(var(--value, 90) * 1deg) }",
        )
    )
    monitor.update({"q": 10, "pitch": "+45.5", "lit": True})
    await expect(page.locator("#q")).to_have_css("width", "50px")  # ratio 0.25
    await expect(page.locator("#pitch")).to_have_css("rotate", "45.5deg")  # device text
    await expect(page.locator("#lit")).to_have_css("--value", "1")

    monitor.set("pitch", "n/a")  # not a number: CSS falls back
    await expect(page.locator("#pitch")).to_have_css("rotate", "90deg")


async def test_stale_after_flags_a_value_that_stopped_arriving(page, serve):
    monitor = Monitor()
    monitor.add(TextElement("wind"), TextElement("tide"))
    url = serve(
        '<span id="wind" data-bind="wind" data-stale-after="1"></span>'
        '<span id="tide" data-bind="tide" data-stale-after="1"></span>'
        '<span id="plain" data-bind="wind"></span>',
        monitor,
    )
    await page.goto(url)
    wind, tide = page.locator("#wind"), page.locator("#tide")
    await expect(tide).to_have_attribute("data-stale", "true")  # nothing ever arrived
    await expect(wind).to_have_attribute("data-stale", "true")

    for _ in range(8):  # a steady value still counts as arriving
        monitor.set("wind", 12)
        await asyncio.sleep(0.25)
        await expect(wind).not_to_have_attribute("data-stale", "true")
    await expect(wind).to_have_attribute("data-stale", "true")  # stopped arriving
    await expect(page.locator("#plain")).not_to_have_attribute("data-stale", "true")

    await page.reload()  # a fresh page knows how old the values are
    await expect(wind).to_have_text("12")
    await expect(wind).to_have_attribute("data-stale", "true")
    monitor.set("wind", 14)
    await expect(wind).not_to_have_attribute("data-stale", "true")


async def test_stale_after_holds_while_the_page_is_disconnected(page, serve):
    """Cut off from the dashboard, the page can't tell whether values still
    arrive there, so it doesn't claim they stopped."""
    monitor = Monitor()
    body = '<span id="wind" data-bind="wind" data-stale-after="2"></span>'
    await page.goto(serve(body, monitor))
    wind = page.locator("#wind")
    monitor.set("wind", 12)
    await expect(wind).not_to_have_attribute("data-stale", "true")
    monitor.stop()
    await expect(page.locator("html")).to_have_attribute("data-connection", "closed")
    await asyncio.sleep(3)
    await expect(wind).not_to_have_attribute("data-stale", "true")


@pytest.mark.parametrize("theme", ["classic", "terminal"])
async def test_generic_page_gives_a_log_the_full_card_width(page, serve, theme):
    monitor = Monitor()
    monitor.add(LogMonitor("events", lines=12, label="Events"))
    await page.set_viewport_size({"width": 1280, "height": 800})
    await page.goto(serve(None, monitor, theme=theme))
    line = "09:50:05 WARNING job 1258 failed (upstream timed out), retry " + "1" * 9
    assert len(line) == 70
    for _ in range(12):
        monitor.set("events", line)
    row = page.locator(".row", has_text="Events")
    await expect(row.locator('[data-mode="log"] > *')).to_have_count(12)
    box = await row.evaluate(
        """row => {
            const log = row.querySelector('[data-mode="log"]');
            const card = row.closest('section').getBoundingClientRect();
            const label = row.querySelector('.label').getBoundingClientRect();
            const box = log.getBoundingClientRect();
            const lines = [...log.children].map(l => l.getBoundingClientRect().height);
            return {log: box.width, card: card.width, above: label.bottom <= box.top,
                    tall: Math.max(...lines), small: Math.min(...lines)};
        }"""
    )
    assert box["above"]
    assert box["log"] > box["card"] - 40  # the card's padding only
    assert box["tall"] - box["small"] < 1  # no line wrapped onto a second row


async def test_themes_switch_and_are_remembered(page, serve):
    monitor = Monitor()
    body = (
        '<button id="theme" data-theme-switch>Terminal</button>'
        '<button id="crt" data-crt-switch>CRT</button>'
    )
    head = '<script src="/_sightglass/theme.js"></script>'
    await page.goto(serve(body, monitor, theme="terminal", head=head))
    html, theme, crt = (
        page.locator("html"),
        page.locator("#theme"),
        page.locator("#crt"),
    )
    await expect(html).to_have_attribute("data-theme", "terminal")  # the default
    await expect(html).to_have_attribute("data-crt", "on")
    await expect(theme).to_have_attribute("aria-pressed", "true")

    await theme.click()
    await crt.click()
    await expect(html).to_have_attribute("data-theme", "classic")
    await expect(html).to_have_attribute("data-crt", "off")
    await expect(theme).to_have_attribute("aria-pressed", "false")
    await expect(crt).to_have_attribute("aria-pressed", "false")

    await page.reload()  # the viewer's choice beats the dashboard's default
    await expect(html).to_have_attribute("data-theme", "classic")
    await expect(html).to_have_attribute("data-crt", "off")


async def test_generic_page_shows_the_programs_own_resources(page, serve):
    """ProcessSource with no other code: CPU, memory in MB, threads, uptime."""
    monitor = Monitor()
    source = ProcessSource(interval=0.1)
    await page.goto(serve(None, monitor, sources=[source]))
    memory = page.locator(".row", has_text="Memory")
    await expect(memory).to_contain_text(re.compile(r"\d+\.\d MB"))
    await expect(page.locator(".row", has_text="CPU")).to_contain_text(
        re.compile(r"\d+\.\d ?%")
    )
    await expect(page.locator(".row", has_text="Threads")).to_contain_text(
        re.compile(r"\d")
    )
    await expect(page.locator(".row", has_text="Uptime")).to_contain_text(" s")


@pytest.mark.parametrize("theme", ["classic", "terminal"])
async def test_counters_share_one_row_and_wrap_on_a_phone(page, serve, theme):
    monitor = Monitor()
    fields = ["submitted", "done", "retried", "failed"]
    monitor.add(Counters("jobs.totals", fields, label="Totals"))
    monitor.update(
        {f"jobs.totals.{f}": n for f, n in zip(fields, (44, 41, 3, 2), strict=True)}
    )
    await page.goto(serve(None, monitor, theme=theme))
    cells = page.locator(".counter b")
    await expect(cells).to_have_text(["44", "41", "3", "2"])

    async def tops():
        return await page.locator(".counter").evaluate_all(
            "els => els.map(e => Math.round(e.getBoundingClientRect().top))"
        )

    assert len(set(await tops())) == 1  # desktop: one row
    await page.set_viewport_size({"width": 390, "height": 800})
    narrow = await page.evaluate("document.documentElement.scrollWidth")
    assert narrow <= 390  # wraps rather than overflowing


async def test_generic_page_offers_the_terminal_theme(page, serve):
    monitor = Monitor()
    monitor.add(ProgressBar("job", label="Job"))
    await page.goto(serve(None, monitor, theme="terminal"))
    await expect(page.locator("html")).to_have_attribute("data-theme", "terminal")
    font = await page.locator("body").evaluate("el => getComputedStyle(el).fontFamily")
    assert font.startswith("VT323")
    await page.locator("[data-theme-switch]").click()
    font = await page.locator("body").evaluate("el => getComputedStyle(el).fontFamily")
    assert "VT323" not in font


# The CRT effects' layers, and the CSS animations running on the page.
SCANLINES = "getComputedStyle(document.body, '::before').content"
ANIMATIONS = "document.getAnimations().filter(a => a instanceof CSSAnimation).length"


async def test_crt_effects_follow_the_switch_and_reduced_motion(page, serve):
    """Still effects need only the CRT switch; moving ones also stop for people
    who ask for reduced motion."""
    monitor = Monitor()
    monitor.add(ProgressBar("job", label="Job"))
    await page.goto(serve(None, monitor, theme="terminal"))
    assert await page.evaluate(SCANLINES) == '""'
    assert await page.evaluate(ANIMATIONS) > 0  # the refresh rolling, the grain

    await page.emulate_media(reduced_motion="reduce")
    await page.reload()
    assert await page.evaluate(SCANLINES) == '""'  # still there
    assert await page.evaluate(ANIMATIONS) == 0  # nothing moves, not even power-on

    await page.emulate_media(reduced_motion="no-preference")
    await page.locator("[data-crt-switch]").click()  # off
    assert await page.evaluate(SCANLINES) == "none"
    assert await page.evaluate(ANIMATIONS) == 0


async def test_page_marks_quiet_sources_and_a_lost_connection(page, serve):
    monitor = Monitor()
    await page.goto(serve('<span data-bind="x"></span>', monitor, stale_after=1))
    html = page.locator("html")
    monitor.set("x", 1)
    await expect(html).to_have_attribute("data-stale", "true")
    monitor.set("x", 2)
    await expect(html).not_to_have_attribute("data-stale", "true")
    monitor.stop()
    await expect(html).to_have_attribute("data-connection", "closed")


async def test_generic_page_draws_declared_and_new_elements(page, serve):
    monitor = Monitor()
    monitor.add(ProgressBar("job", label="Job"))
    await page.goto(serve(None, monitor, title="Line 3"))
    await expect(page).to_have_title("Line 3")
    monitor.update({"job": 25, "status": "ok"})
    await expect(page.locator(".row", has_text="Job")).to_contain_text("25.0%")
    await expect(page.locator(".row", has_text="status")).to_contain_text("ok")
    await expect(page.locator("#connection")).to_have_text("Live")


async def test_generic_page_shows_units_once_for_each_element(page, serve):
    monitor = Monitor()
    monitor.add(
        TextElement("uptime", label="Uptime", units="s"),
        Sparkline("cpu", label="Cpu", units="%"),
        RangeBar("temp", label="Temp", units="degC"),
        ProgressBar("job", label="Job"),
        Coordinate("pos", label="Pos", axes=("x",), units="mm"),
    )
    await page.goto(serve(None, monitor))
    monitor.update({"uptime": 7, "cpu": 8.8, "temp": 50, "job": 25, "pos": {"x": 1}})
    for label, text in [
        ("Uptime", "7 s"),
        ("Cpu", "8.8 %"),
        ("Temp", "50.00 degC"),
        ("Job", "25.0%"),
        ("Pos", "X +1.0000 mm"),
    ]:
        row = page.locator(".row", has_text=label)
        await expect(row).to_contain_text(text)
        assert (await row.inner_text()).count(text.split()[-1]) == 1


async def test_log_binding_marks_each_lines_level(page, serve):
    monitor = Monitor()
    monitor.add(LogMonitor("log", lines=5))
    await page.goto(
        serve('<div id="log" data-bind="log" data-mode="log"></div>', monitor)
    )
    for line in [
        "12:00:01 INFO started",
        "WARNING: disk 91% full",
        "12:00:03 ERROR job 7 gave up",
        "CRITICAL down",
        "no error here",
    ]:
        monitor.set("log", line)
    lines = page.locator("#log > *")
    await expect(lines).to_have_count(5)
    await expect(lines.nth(4)).to_have_text("no error here")
    levels = [await lines.nth(n).get_attribute("data-level") for n in range(5)]
    assert levels == ["info", "warning", "error", "error", None]


async def test_generic_page_shows_errors_in_a_log_apart_from_the_rest(page, serve):
    monitor = Monitor()
    monitor.add(LogMonitor("events", lines=3, label="Events"))
    await page.goto(serve(None, monitor))
    monitor.set("events", "INFO fine")
    monitor.set("events", "ERROR broken")
    row = page.locator(".row", has_text="Events")
    error = row.locator('[data-level="error"]')
    await expect(error).to_have_text("ERROR broken")
    colours = await row.evaluate(
        """row => [...row.querySelectorAll('[data-mode="log"] > *')]
            .map(line => getComputedStyle(line).color)"""
    )
    assert colours[0] != colours[1]


async def test_signals_view_lists_every_value_sent_with_its_last_minute(page, serve):
    monitor = Monitor()
    monitor.add(RangeBar("speed", max=200, units="km/h"))
    monitor.update({"speed": 50, "axis10.pos": 2, "axis2.pos": 1, "status": "ok"})
    sockets = []
    page.on("websocket", lambda socket: sockets.append(socket))
    await page.goto(serve(None, monitor))
    await page.locator("[data-signals-open]").click()

    view = page.locator("sightglass-signals")  # locators reach into its shadow root
    await expect(view.locator("dialog")).to_be_visible()
    await expect(view).to_have_attribute("data-scheme", "system")  # light or dark
    rows = view.locator("tbody tr")
    await expect(rows.locator("th")).to_have_text(  # numbers in ids count as numbers
        ["axis2.pos", "axis10.pos", "speed", "status"]
    )
    await expect(view.locator(".count")).to_have_text("4")
    speed = rows.filter(has_text="speed")
    await expect(speed.locator(".value")).to_have_text("50")  # not "50.00 km/h"
    await expect(speed.locator("path")).to_have_attribute("d", re.compile(r"^M"))
    status = rows.filter(has_text="status")
    await expect(status.locator(".value")).to_have_text("ok")
    await expect(status.locator("path")).not_to_have_attribute("d", re.compile(""))

    monitor.update({"speed": 120, "pump": True})
    await expect(speed.locator(".value")).to_have_text("120")
    await expect(rows).to_have_count(5)

    await page.keyboard.type("AXIS")  # typing filters, by id, ignoring case
    await expect(view.locator("input")).to_have_value("AXIS")
    await expect(view.locator(".count")).to_have_text("2 of 5")
    await expect(view.locator("tbody tr:not([hidden]) th")).to_have_text(
        ["axis2.pos", "axis10.pos"]
    )
    await page.keyboard.type("x")
    empty = view.locator(".empty")
    await expect(empty).to_have_text("No signal matches \u201cAXISx\u201d.")

    await view.locator(".close").click()
    await expect(view.locator("dialog")).to_be_hidden()
    feed = next(s for s in sockets if s.url.endswith("/ws/signals"))
    await until(feed.is_closed)  # nothing streams while it's closed


class Ticking:
    """A polling source that answers every 0.2 s: 5 Hz."""

    def __init__(self):
        self.feeds = [Feed("http://plant/api")]

    async def updates(self):
        loop = asyncio.get_running_loop()
        n = 0
        while True:
            began = loop.time()
            await asyncio.sleep(0.2)
            self.feeds[0].record(loop.time(), loop.time() - began, failed=False)
            n += 1
            yield [{"speed": n}]


async def test_signals_view_wraps_long_ids_on_a_phone_instead_of_clipping_them(
    page, serve
):
    monitor = Monitor()
    ids = [f"jobs.totals.count.{leaf}" for leaf in ("submitted", "done", "retried")]
    monitor.update({i: 1 for i in ids})
    await page.set_viewport_size({"width": 390, "height": 800})
    await page.goto(serve(None, monitor))
    await page.locator("[data-signals-open]").click()

    names = page.locator("sightglass-signals tbody th")
    await expect(names).to_have_count(3)
    clipped = await names.evaluate_all(
        "els => els.map(e => e.scrollWidth > e.clientWidth + 1)"
    )
    assert clipped == [False, False, False]


async def test_signals_view_shows_how_fast_the_sources_poll(page, serve):
    monitor = Monitor()
    await page.goto(serve(None, monitor, sources=[Ticking()], debug=True))
    await page.locator("[data-signals-open]").click()
    stats = page.locator("sightglass-signals .stats")
    await expect(stats.locator("h3", has_text="plant/api")).to_be_visible(
        timeout=15_000
    )
    polls = stats.locator("[data-stat=polls] b")
    await expect(polls).to_have_text(re.compile(r"^[45]\.\d\d$"), timeout=15_000)
    await expect(stats.locator("[data-stat=took] b")).to_have_text(
        re.compile(r"^2\d\d ms$")
    )


async def test_signals_view_shows_whether_the_page_keeps_up(page, serve):
    monitor = Monitor()
    await page.goto(serve(None, monitor, sources=[Ticking()], debug=True))
    await page.locator("[data-signals-open]").click()
    stats = page.locator("sightglass-signals .stats")
    number = lambda key: stats.locator(f"[data-stat={key}] b")  # noqa: E731
    await expect(number("frames")).to_have_text(re.compile(r"^\d+$"), timeout=15_000)
    await expect(number("messages")).to_have_text(re.compile(r"^[3-6]$"))
    await expect(number("merged")).to_have_text("0")
    await expect(number("waited")).to_have_text(re.compile(r"^\d+ ms$"))
    await expect(stats.locator("[data-stat=waited].bad")).to_have_count(0)
    # a page that is blocked is behind, and says so
    await page.evaluate(
        "() => { const end = Date.now() + 1500; while (Date.now() < end); }"
    )
    await expect(stats.locator("[data-stat=gap].bad")).to_have_count(1, timeout=5_000)


async def test_signals_view_shows_how_quickly_plots_get_their_points(page, serve):
    monitor = Monitor(history=60)
    monitor.set("a", 1)
    body = '<div data-plot="a" style="height: 100px"></div>'
    await page.goto(serve(body, monitor, debug=True))
    await page.locator("body").press("`")
    stats = page.locator("sightglass-signals .stats")
    await expect(stats.locator("[data-stat=requests] b")).to_have_text(
        re.compile(r"^\d+$"), timeout=10_000
    )
    await expect(stats.locator("[data-stat=slowest]")).to_be_visible()


async def test_signals_view_has_no_figures_unless_the_dashboard_is_in_debug(
    page, serve
):
    monitor = Monitor()
    await page.goto(serve(None, monitor, sources=[Ticking()]))
    await page.locator("[data-signals-open]").click()
    view = page.locator("sightglass-signals")
    await expect(view.locator("tbody tr")).to_have_count(1)  # it is up and has data
    await page.wait_for_timeout(2500)
    await expect(view.locator(".stat")).to_have_count(0)


async def test_signals_view_charts_are_one_line_forward_in_time(page, serve):
    """A switch flipping, or a value swinging within half a second, is one line
    through its lowest and highest values: never an outline doubling back."""
    monitor = Monitor()
    monitor.set("pump", True)
    await page.goto(serve(None, monitor))
    await page.keyboard.press("Backquote")
    pump = page.locator("sightglass-signals tbody tr")
    for value in [False, True, False, True, False]:  # across two or three slots
        monitor.set("pump", value)
        await asyncio.sleep(0.2)
    await expect(pump.locator(".value")).to_have_text("False")
    await page.wait_for_timeout(100)  # drawn on the next frame
    line = await pump.locator("path").get_attribute("d")
    assert "Z" not in line
    xs = [float(x) for x in re.findall(r"[ML](-?[\d.]+),", line)]
    assert len(xs) >= 4
    assert xs == sorted(xs)  # never back over itself


async def test_signals_view_opens_on_any_page_with_the_backquote_key(page, serve):
    monitor = Monitor()
    monitor.set("pump", "on")
    head = (
        '<script src="/_sightglass/theme.js"></script>'
        '<link rel="stylesheet" href="/_sightglass/terminal.css">'
    )
    await page.goto(serve('<input id="note">', monitor, theme="terminal", head=head))
    await expect(page.locator("html")).to_have_attribute("data-connection", "open")
    note = page.locator("#note")
    await note.press_sequentially("a`b")  # typing a ` in the page is just typing
    await expect(note).to_have_value("a`b")
    await expect(page.locator("sightglass-signals")).to_have_count(0)

    await note.blur()
    await page.keyboard.press("Backquote")
    view = page.locator("sightglass-signals")
    await expect(view.locator("dialog")).to_be_visible()
    await expect(view.locator("tbody th")).to_have_text(["pump"])
    await expect(view.locator("tbody .value")).to_have_text("on")
    await expect(view.locator("tbody path")).to_have_attribute("d", re.compile(r"^M"))
    # It follows the page: its theme, and its color-scheme (terminal.css: dark).
    await expect(view).to_have_attribute("data-theme", "terminal")
    await expect(view).to_have_attribute("data-scheme", "dark")
    await page.evaluate("document.documentElement.dataset.theme = 'classic'")
    await expect(view).to_have_attribute("data-theme", "classic")

    await page.keyboard.press("Backquote")
    await expect(view.locator("dialog")).to_be_hidden()
    await page.keyboard.press("Backquote")  # and again
    await expect(view.locator("dialog")).to_be_visible()
    await page.keyboard.press("Escape")
    await expect(view.locator("dialog")).to_be_hidden()


async def test_launch_demo_page_comes_alive(page):
    """sightglass --demo launch: every feed arrives and the instruments move."""
    process = await asyncio.create_subprocess_exec(
        *[sys.executable, "-m", "sightglass", "--demo", "launch", "--port", "0"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        url = (await process.stderr.readline()).decode().split()[1]
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else 0)
        await page.goto(url)
        clock = page.locator('.clock [data-bind="clock.time"]')
        await expect(clock).to_have_text(re.compile(r"^\d\d:\d\d:\d\d$"))
        await expect(page.locator(".feeds .led[data-bind]")).to_have_count(3)
        # The feeders are separate processes: allow them time to start.
        await expect(page.locator(".feeds [data-stale]")).to_have_count(
            0, timeout=15_000
        )
        gauge = page.locator('[data-bind="acceleration.ratio"]')
        await expect(gauge).to_have_css("--value", "0.2")  # 1 g, standing on the pad
        await expect(page.locator(".weather .chart polyline")).to_have_count(1)

        await expect(page.locator("html")).to_have_attribute("data-theme", "classic")
        await page.locator("[data-theme-switch]").click()  # the terminal theme
        clock = page.locator(".clock")
        await expect(page.locator("html")).to_have_attribute("data-theme", "terminal")
        font = await clock.evaluate("el => getComputedStyle(el).fontFamily")
        assert font.startswith('"Micro 5"')
        # Its block digits, and the readings' pixel ones, really arrived.
        for face in ['"Micro 5"', "Silkscreen"]:
            loaded = await page.evaluate(
                "face => document.fonts.load(`20px ${face}`).then(f => f.length)", face
            )
            assert loaded, face
        await page.emulate_media(reduced_motion="reduce")
        assert await page.evaluate(ANIMATIONS) == 0  # the cursor stops blinking
        assert not errors
    finally:
        process.terminate()
        await asyncio.wait_for(process.communicate(), 10)


# How many pixels of a plot's canvas are drawn in red.
RED_PIXELS = """el => {
  const canvas = el.querySelector("canvas");
  const { width, height } = canvas;
  const data = canvas.getContext("2d").getImageData(0, 0, width, height).data;
  let red = 0;
  for (let i = 0; i < data.length; i += 4) {
    if (data[i] > 200 && data[i + 1] < 80 && data[i + 3] > 60) red++;
  }
  return red;
}"""


async def test_plot_draws_ids_against_time_and_keeps_up(page, serve):
    """A data-plot draws its ids (patterns too) from the history, then follows
    new values: a spike one sample long reaches the plot."""
    monitor = Monitor(history=60)
    plot = (
        '<div id="plot" data-plot="wave.* level" data-seconds="10"'
        ' style="width: 400px; height: 200px; color: rgb(255, 0, 0)"></div>'
    )
    monitor.update({"wave.1": 1, "wave.2": 1.2, "level": 1.1})
    await page.goto(serve(plot, monitor))
    await expect(page.locator("#plot")).to_have_attribute("data-plot-signals", "3")
    await expect(page.locator("#plot")).to_have_attribute("data-plot-range", "1 1.2")
    assert await page.locator("#plot").evaluate(RED_PIXELS) > 50  # lines and labels
    assert await page.locator("#plot canvas").get_attribute("aria-label") == (
        "3 signals over the last 10 s"
    )

    monitor.update({"wave.1": 50})  # a spike, one sample long
    await asyncio.sleep(0.06)
    monitor.update({"wave.1": 1})
    await expect(page.locator("#plot")).to_have_attribute("data-plot-range", "1 50")


# Records what a canvas does, in order, in window.ops.
RECORD_DRAWING = """
window.ops = [];
const proto = CanvasRenderingContext2D.prototype;
for (const name of ["stroke", "strokeText", "fillText"]) {
  const original = proto[name];
  proto[name] = function (...args) {
    window.ops.push(name === "stroke" ? "stroke" : `${name}:${args[0]}`);
    return original.apply(this, args);
  };
}
"""


async def test_plot_value_labels_are_drawn_over_the_lines(page, serve):
    """The value axis's labels come after the lines, each outlined, so no line
    hides them."""
    await page.add_init_script(RECORD_DRAWING)
    monitor = Monitor(history=60)
    monitor.update({"a": 1, "b": 3})
    plot = (
        '<div id="plot" data-plot="a b" data-seconds="10"'
        ' style="width: 400px; height: 200px; background: black"></div>'
    )
    await page.goto(serve(plot, monitor))
    await expect(page.locator("#plot")).to_have_attribute("data-plot-range", "1 3")
    ticks = (await page.locator("#plot").get_attribute("data-plot-ticks")).split()
    ops = await page.evaluate("window.ops")
    after_lines = ops[len(ops) - ops[::-1].index("stroke") :]
    labels = [f"{op}:{tick}" for tick in ticks for op in ("strokeText", "fillText")]
    assert after_lines[: len(labels)] == labels


async def plot_values(page, serve, monitor, attributes, *updates, style=""):
    """Serve a 400 px plot of ``attributes`` fed ``updates`` a sample apart;
    return its element once its range shows the last of them."""
    await page.add_init_script(RECORD_LABELS)
    for update in updates:
        monitor.update(update)
        await asyncio.sleep(0.06)  # a 50 ms slot each
    plot = (
        f'<div id="plot" data-seconds="10" {attributes}'
        f' style="width: 400px; height: 200px; font: 12px sans-serif; {style}"></div>'
    )
    await page.goto(serve(plot, monitor))
    element = page.locator("#plot")
    await expect(element).to_have_attribute("data-plot-ticks", re.compile("."))
    return element


async def test_plot_value_axis_has_round_ticks_in_its_units(page, serve):
    plot = await plot_values(
        page,
        serve,
        Monitor(history=60),
        'data-plot="cpu" data-plot-units="%"',
        {"cpu": 0},
        {"cpu": 100},
    )
    await expect(plot).to_have_attribute("data-plot-ticks", "0 20 40 60 80 100")
    labels = await page.evaluate("[...window.labels.plot]")
    assert {"0 %", "20 %", "100 %"} <= set(labels), labels


async def test_plot_ticks_share_their_decimals(page, serve):
    plot = await plot_values(
        page,
        serve,
        Monitor(history=60),
        'data-plot="p95" data-plot-units="ms"',
        {"p95": 1.2},
        {"p95": 3.7},
    )
    ticks = (await plot.get_attribute("data-plot-ticks")).split()
    assert ticks == ["1.5", "2.0", "2.5", "3.0", "3.5"], ticks


async def test_plot_scale_turns_raw_values_into_the_units_shown(page, serve):
    plot = await plot_values(
        page,
        serve,
        Monitor(history=60),
        'data-plot="rss" data-plot-scale="0.0009765625" data-plot-units="KB"',
        {"rss": 51200},
        {"rss": 102400},
    )
    await expect(plot).to_have_attribute("data-plot-range", "50 100")
    await expect(plot).to_have_attribute("data-plot-ticks", "50 60 70 80 90 100")
    assert "100 KB" in await page.evaluate("[...window.labels.plot]")


async def test_plot_shortens_big_numbers_with_a_prefix(page, serve):
    plot = await plot_values(
        page,
        serve,
        Monitor(history=60),
        'data-plot="rss" data-plot-units="B"',
        {"rss": 46_890_000},
        {"rss": 230_900_000},
    )
    await expect(plot).to_have_attribute("data-plot-ticks", "50M 100M 150M 200M")
    assert "200 MB" in await page.evaluate("[...window.labels.plot]")


# How many of a canvas's pixels are close to each of the given colours.
PIXELS_NEAR = """(el, colours) => {
  const canvas = el.querySelector("canvas");
  const { width, height } = canvas;
  const data = canvas.getContext("2d").getImageData(0, 0, width, height).data;
  const channels = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const rgb = colours.map(channels);
  const counts = colours.map(() => 0);
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 250) continue;
    rgb.forEach(([r, g, b], n) => {
      const off = Math.abs(data[i] - r) + Math.abs(data[i + 1] - g)
        + Math.abs(data[i + 2] - b);
      if (off < 30) counts[n]++;
    });
  }
  return counts;
}"""


async def test_a_few_ids_get_a_colour_each_and_a_legend(page, serve):
    plot = await plot_values(
        page,
        serve,
        Monitor(history=60),
        'data-plot="jobs.queue jobs.p95"',
        {"jobs.queue": 0, "jobs.p95": 3},
        {"jobs.queue": 4, "jobs.p95": 2},
    )
    await expect(plot).to_have_attribute("data-plot-legend", "queue p95")
    labels = await page.evaluate("[...window.labels.plot]")
    assert {"queue", "p95"} <= set(labels)
    blue, orange = await plot.evaluate(PIXELS_NEAR, ["#257de0", "#f47230"])
    assert blue > 50 and orange > 50, (blue, orange)


async def test_a_dark_plot_takes_the_palettes_dark_steps(page, serve):
    plot = await plot_values(
        page,
        serve,
        Monitor(history=60),
        'data-plot="a b"',
        {"a": 0, "b": 3},
        {"a": 4, "b": 2},
        style="background: #111315; color: #e8eaed",
    )
    blue, orange = await plot.evaluate(PIXELS_NEAR, ["#338ced", "#e76624"])
    assert blue > 50 and orange > 50, (blue, orange)


async def test_one_id_or_very_many_keep_the_elements_colour_and_no_legend(page, serve):
    monitor = Monitor(history=60)
    monitor.update({f"wave.{n}": n for n in range(9)})
    plots = "".join(
        f'<div id="{name}" data-plot="{ids}" data-seconds="10"'
        ' style="width: 400px; height: 200px; color: rgb(255, 0, 0)"></div>'
        for name, ids in [("one", "wave.0"), ("many", "wave.*")]
    )
    await page.goto(serve(plots, monitor))
    for name in ("one", "many"):
        plot = page.locator(f"#{name}")
        await expect(plot).to_have_attribute("data-plot-ticks", re.compile("."))
        assert await plot.get_attribute("data-plot-legend") is None
        assert await plot.evaluate(RED_PIXELS) > 50


async def test_a_config_dashboard_wraps_its_panels_and_fills_the_screen(
    page, serve, tmp_path
):
    """Five panels at 1920 x 1080: three panels, then two that share the row, all
    of it on one screen with nothing to scroll; every plot has its values."""
    from sightglass.dashboard import load

    config = tmp_path / "dashboard.toml"
    config.write_text(
        """
[[source]]
url = "http://127.0.0.1:1/x"
[[panel]]
each = ["a", "b", "c", "d", "e"]
title = "{name}"
ids = ["p.{name}"]
range = 10
secondary_range = 30
"""
    )
    monitor = Monitor(history=60)
    monitor.update({f"p.{name}": 1.5 for name in "abcde"})
    await page.set_viewport_size({"width": 1920, "height": 1080})
    await page.goto(serve(None, monitor, html=load(config).page()))
    plots = page.locator("[data-plot]")
    assert await plots.count() == 10
    for i in range(10):
        await expect(plots.nth(i)).to_have_attribute("data-plot-signals", "1")
    boxes = await page.evaluate(
        "[...document.querySelectorAll('.panel')].map(e => {"
        " const r = e.getBoundingClientRect();"
        " return [r.left, r.top, r.right, r.bottom] })"
    )
    rows = {}
    for left, top, right, bottom in boxes:
        rows.setdefault(round(top), []).append((left, right, bottom))
    first, second = (rows[top] for top in sorted(rows))
    assert (len(first), len(second)) == (3, 2)
    assert second[0][1] - second[0][0] > 1.4 * (first[0][1] - first[0][0])
    assert abs(first[-1][1] - second[-1][1]) < 2  # both rows reach the right edge
    assert max(box[2] for row in rows.values() for box in row) <= 1080
    assert await page.evaluate(
        "(() => { const g = document.querySelector('main');"
        " return g.scrollHeight <= g.clientHeight + 1 })()"
    )


async def test_a_plot_only_page_can_opt_out_of_live_values(page, serve):
    """data-values="off" on the script tag: the plots still draw, from /history,
    but the page opens no websocket for values it never shows."""
    monitor = Monitor(history=30)
    monitor.update({"a": 1.5})
    sockets = []
    page.on("websocket", lambda ws: sockets.append(ws.url))
    plot = '<div id="p" data-plot="a" style="width: 300px; height: 100px"></div>'

    def html(attribute):
        return (
            f"<!doctype html><body>{plot}"
            f'<script src="/_sightglass/sightglass.js"{attribute}></script></body>'
        )

    await page.goto(serve(None, monitor, html=html("")))
    await expect(page.locator("#p")).to_have_attribute("data-plot-signals", "1")
    assert len(sockets) == 1 and sockets[0].endswith("/ws")  # the usual page

    sockets.clear()
    quiet = Monitor(history=30)
    quiet.update({"a": 1.5})
    await page.goto(serve(None, quiet, html=html(' data-values="off"')))
    await expect(page.locator("#p")).to_have_attribute("data-plot-signals", "1")
    await page.wait_for_timeout(300)
    assert sockets == []
    assert await page.evaluate("document.documentElement.dataset.connection") == "off"


# Records the text each plot draws, by the id of its element, in window.labels.
RECORD_LABELS = """
window.labels = {};
const fillText = CanvasRenderingContext2D.prototype.fillText;
CanvasRenderingContext2D.prototype.fillText = function (text, ...rest) {
  const id = this.canvas.parentElement?.id;
  if (id) (window.labels[id] ??= new Set()).add(text);
  return fillText.call(this, text, ...rest);
};
"""


async def test_plot_time_axis_uses_one_unit_for_all_its_labels(page, serve):
    """A wide plot of 5 minutes has labels every 30 s: all in seconds, never
    "-5 min" next to "-270 s". A narrower one, labelled every minute, is all in
    minutes."""
    await page.add_init_script(RECORD_LABELS)
    monitor = Monitor(history=300)
    monitor.set("a", 1)
    plots = "".join(
        f'<div id="{name}" data-plot="a" data-seconds="300"'
        f' style="width: {width}px; height: 200px; font: 12px sans-serif"></div>'
        for name, width in [("wide", 1000), ("narrow", 400)]
    )
    await page.goto(serve(plots, monitor))
    for name in ("wide", "narrow"):
        await expect(page.locator(f"#{name}")).to_have_attribute(
            "data-plot-signals", "1"
        )
    await page.wait_for_timeout(300)
    labels = await page.evaluate(
        "Object.fromEntries(Object.entries(window.labels).map(([k, v]) => [k, [...v]]))"
    )
    wide = {t for t in labels["wide"] if t != "now" and t[0] == "−"}
    narrow = {t for t in labels["narrow"] if t != "now" and t[0] == "−"}
    assert "−270 s" in wide and not any("min" in t for t in wide), wide
    assert "−1 min" in narrow and not any(t.endswith(" s") for t in narrow), narrow


# For each third of a heat map's cells (its three rows), how many pixels are
# drawn at full strength, and how many at all.
HEAT_ROWS = """el => {
  const canvas = el.querySelector("canvas");
  const { width, height } = canvas;
  const data = canvas.getContext("2d").getImageData(0, 0, width, height).data;
  const lines = height - 26 * devicePixelRatio;  // above the 16px time axis
  const rows = [0, 1, 2].map(() => ({ strong: 0, drawn: 0 }));
  for (let y = 0; y < lines; y++) {
    const row = rows[Math.floor((y / lines) * 3)];
    for (let x = 0; x < width; x++) {
      const alpha = data[(y * width + x) * 4 + 3];
      if (alpha > 240) row.strong++;
      if (alpha > 0) row.drawn++;
    }
  }
  return rows;
}"""


async def test_heatmap_gives_each_id_a_row_where_a_spike_stands_out(page, serve):
    """A row per id, each cell as far as its value is from the id's usual one:
    a wave that keeps swinging, or ramps, stays faint; a steady value's one
    spike is at full strength."""
    monitor = Monitor(history=60)
    plot = (
        '<div id="heat" data-plot="a b c" data-plot-mode="heatmap"'
        ' data-seconds="3" style="width: 300px; height: 176px; font: 16px sans-serif;'
        ' color: rgb(0, 0, 255)"></div>'
    )
    url = serve(plot, monitor)
    sent = 0

    async def feed():  # live, 20 times a second, as a PLC would be
        nonlocal sent
        while True:
            k = sent
            monitor.update({"a": 1 + k % 2, "b": 50 if k == 30 else 1, "c": k})
            sent += 1
            await asyncio.sleep(0.05)

    feeding = asyncio.create_task(feed())
    try:
        await page.goto(url)
        await expect(page.locator("#heat")).to_have_attribute("data-plot-signals", "3")
        await until(lambda: sent >= 50, within=5)  # the spike, then more
        await page.wait_for_timeout(200)  # drawn with the latest points
        swing, spike, ramp = await page.locator("#heat").evaluate(HEAT_ROWS)
    finally:
        feeding.cancel()
    assert spike["strong"] > 0
    assert swing["strong"] == ramp["strong"] == 0, (swing, spike, ramp)
    assert swing["drawn"] > 10 * spike["strong"]  # faint, but there


async def test_plot_says_why_it_has_nothing_without_history(page, serve):
    plot = '<div id="plot" data-plot="speed" style="height: 100px"></div>'
    await page.goto(serve(plot, Monitor()))
    await expect(page.locator("#plot")).to_have_attribute(
        "data-plot-error", re.compile("--history")
    )


async def test_plot_demo_page_comes_alive(page):
    """sightglass --demo plot: both plots draw all 100 waves."""
    process = await asyncio.create_subprocess_exec(
        *[sys.executable, "-m", "sightglass", "--demo", "plot", "--port", "0"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        url = (await process.stderr.readline()).decode().split()[1]
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else 0)
        await page.goto(url)
        plots = page.locator("[data-plot]")
        await expect(plots).to_have_count(2)
        for i in range(2):
            await expect(plots.nth(i)).to_have_attribute("data-plot-signals", "100")
        assert not errors
    finally:
        process.terminate()
        await asyncio.wait_for(process.communicate(), 10)


@pytest.mark.skipif(sys.platform == "win32", reason="pyads needs TwinCAT on Windows")
async def test_load_demo_reads_a_plc_and_measures_itself(page):
    """sightglass --demo load: the fake PLC's 1000 signals arrive over ADS,
    the heat map draws them all, and the readings are measured."""
    process = await asyncio.create_subprocess_exec(
        *[sys.executable, "-m", "sightglass", "--demo", "load", "--port", "0"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        line = (await asyncio.wait_for(process.stderr.readline(), 20)).decode()
        assert line.startswith("Dashboard: "), line
        url = line.split()[1]
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else 0)
        await page.goto(url)
        heat = page.locator('[data-plot-mode="heatmap"]')
        signals = expect(heat).to_have_attribute
        await signals("data-plot-signals", "1000", timeout=10_000)
        await expect(page.locator('[data-bind="load.signals"]')).to_have_text("1000")
        reading = page.locator('.reading[data-bind="load.values"]')
        await expect(reading).to_have_text(re.compile(r"^\d{4,}$"), timeout=5_000)
        assert not errors
    finally:
        process.terminate()
        await asyncio.wait_for(process.communicate(), 15)


async def test_value_lamp_and_gauge_panels_follow_the_values_and_blank_when_invalid(
    page, serve, tmp_path
):
    """A readings section above a plots section: the reading, the light and the
    bar show live values, and an invalid one (blank) shows nothing, an unlit
    light and an empty bar, never the last value."""
    from sightglass.dashboard import load

    config = tmp_path / "dashboard.toml"
    config.write_text(
        """
[[source]]
url = "http://127.0.0.1:1/x"
[[section]]
title = "Now"
[[section.panel]]
type = "gauge"
title = "Pressure"
id = "p.pressure"
units = "bar"
precision = 1
min = 0
max = 10
[[section.panel]]
type = "value"
title = "Count"
id = "p.count"
[[section.panel]]
type = "lamp"
title = "Running"
id = "p.running"
[[section]]
title = "Last minute"
[[section.panel]]
title = "Pressure"
ids = ["p.pressure"]
range = 60
secondary = false
"""
    )
    dashboard = load(config)
    monitor = Monitor(history=60)
    monitor.add(*dashboard.elements())
    monitor.update({"p.pressure": 2.5, "p.count": 7, "p.running": True})
    await page.set_viewport_size({"width": 1920, "height": 1080})
    await page.goto(serve(None, monitor, html=dashboard.page()))
    reading = page.locator(".gauge .reading span")
    light = page.locator(".light")
    await expect(reading).to_have_text("2.5")
    await expect(page.locator(".value .reading span")).to_have_text("7")
    await expect(light).to_have_attribute("data-state", "on")

    async def fill() -> float:
        return await page.evaluate(
            "(() => { const f = document.querySelector('.fill');"
            " return f.getBoundingClientRect().width"
            " / f.parentElement.getBoundingClientRect().width })()"
        )

    assert abs(await fill() - 0.25) < 0.01  # 2.5 of 0 to 10
    heights = await page.evaluate(
        "[...document.querySelectorAll('main > section')]"
        ".map(s => s.getBoundingClientRect().height)"
    )
    assert heights[1] > heights[0]  # the plots get more height than the readings
    assert await page.evaluate(
        "(() => { const m = document.querySelector('main');"
        " return m.scrollHeight <= m.clientHeight + 1 })()"
    )

    monitor.update({"p.pressure": "", "p.running": ""})
    await expect(reading).to_have_text("")
    await expect(light).to_have_attribute("data-state", "off")
    assert await fill() == 0  # applied with the reading


async def test_a_trends_plots_follow_their_order_and_ratio_and_the_title_bar_moves(
    page, serve, tmp_path
):
    """The secondary plot is half the primary's height and above it by default;
    first = "primary" swaps them; the identity is at the right of the title bar,
    which title_bar = "bottom" puts under the panels."""
    from sightglass.dashboard import load

    async def laid_out(top_level: str, panel: str):
        config = tmp_path / "dashboard.toml"
        config.write_text(
            f'{top_level}[[source]]\nurl = "http://127.0.0.1:1/x"\n'
            f'[[panel]]\nids = ["p.a"]\n{panel}'
        )
        monitor = Monitor(history=400)
        monitor.update({"p.a": 1.5})
        await page.set_viewport_size({"width": 1920, "height": 1080})
        await page.goto(serve(None, monitor, html=load(config).page()))
        await expect(page.locator("[data-plot]").first).to_have_attribute(
            "data-plot-signals", "1"
        )
        return await page.evaluate(
            "(() => { const box = (s) => document.querySelector(s)"
            ".getBoundingClientRect();"
            " const [a, b, h, i] = ['.secondary', '.primary', 'header', '.identity']"
            ".map(box);"
            " return {secondary: [a.top, a.height], primary: [b.top, b.height],"
            " header: [h.top, h.bottom], identity: [i.left, i.right],"
            " width: document.documentElement.clientWidth,"
            " main: document.querySelector('main').getBoundingClientRect().top} })()"
        )

    found = await laid_out("", "")
    assert found["secondary"][0] < found["primary"][0]  # secondary on top
    assert abs(found["secondary"][1] / found["primary"][1] - 0.5) < 0.02
    assert found["header"][1] <= found["main"] + 1  # the title bar is above
    assert found["identity"][1] > found["width"] - 40  # identity at the right

    found = await laid_out('title_bar = "bottom"\n', 'first = "primary"\n')
    assert found["primary"][0] < found["secondary"][0]  # primary on top
    assert found["header"][0] > found["primary"][0]  # the title bar is below


@pytest.mark.parametrize("theme", ["classic", "terminal"])
@pytest.mark.parametrize("width", [1280, 390])
async def test_a_log_panel_shows_the_newest_lines_at_the_bottom_in_both_themes(
    page, serve, tmp_path, theme, width
):
    """The ``log`` panel of a config file: the last lines of a ``LogMonitor``,
    oldest at the top, a warning in its colour, long lines cut off to the
    panel's width rather than widening the page."""
    from sightglass.dashboard import load

    config = tmp_path / "dashboard.toml"
    config.write_text(
        f"""
theme = "{theme}"
[[source]]
url = "http://127.0.0.1:1/x"
[[panel]]
type = "log"
title = "Events"
id = "svc.events"
lines = 6
"""
    )
    dashboard = load(config)
    monitor = Monitor()
    monitor.add(*dashboard.elements())
    for i in range(1, 10):
        level = "WARNING" if i == 9 else "INFO"
        monitor.set("svc.events", f"{level} event {i} " + "long " * 40)
    await page.set_viewport_size({"width": width, "height": 800})
    await page.goto(serve(None, monitor, html=dashboard.page(), theme=theme))
    rows = page.locator(".panel.log .lines > div")
    await expect(rows).to_have_count(6)  # the monitor keeps the last six
    await expect(rows.first).to_contain_text("event 4")
    await expect(rows.last).to_contain_text("event 9")
    await expect(rows.last).to_have_attribute("data-level", "warning")
    assert await page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth"
    )
    box = await page.locator(".panel.log").bounding_box()
    last = await rows.last.bounding_box()
    assert (
        box["y"] <= last["y"]
        and last["y"] + last["height"] <= box["y"] + box["height"] + 1
    )
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
