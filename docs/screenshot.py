"""Take the README's screenshots of the launch demo: its page in both themes,
its signals view, and its terminal screen; then of the plot and load demos.

    uv run --all-extras python docs/screenshot.py         # all of them, ~5 min
    uv run --all-extras python docs/screenshot.py plot    # the plot demo, ~70 s
    uv run --all-extras python docs/screenshot.py load    # the load demo, ~100 s

Runs `sightglass --demo launch --terminal` from the start of its countdown in
a 120 x 36 pseudo-terminal, as someone trying it would (on port 8080, as the
README shows). 52 seconds after liftoff (about 80 s from now), just past
maximum aerodynamic pressure, it photographs the page the demo serves (and
its signals view, the minute up to then) and draws what it printed with
xterm.js, a real terminal emulator (fetched from jsDelivr). Needs Chromium
once: uv run playwright install chromium. macOS and Linux (it uses a pty).
"""

import fcntl
import json
import os
import pty
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

DOCS = Path(__file__).parent
MOMENT = 30 + 52  # seconds from starting: the countdown, then 52 s of flight
COLUMNS, LINES = 120, 36

TERMINAL = """<!doctype html>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.css">
<script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@xterm/addon-webgl@0.18.0/lib/addon-webgl.js"></script>
<style>
  body { margin: 0 }
  #t { display: inline-block; padding: 28px; background: #0a0c0f }
</style>
<div id="t"></div>
<script>
  const term = new Terminal({
    cols: COLUMNS, rows: LINES, fontSize: 28, lineHeight: 1,
    fontFamily: "Menlo, 'DejaVu Sans Mono', monospace",
    theme: { background: "#0a0c0f", foreground: "#d0d0d0" },
  });
  term.open(document.getElementById("t"));
  term.loadAddon(new WebglAddon.WebglAddon());  // exact block and line glyphs
  term.write(FRAME, () => { window.drawn = true; });
</script>
"""


def main() -> None:
    controller, terminal = pty.openpty()
    size = struct.pack("HHHH", LINES, COLUMNS, 0, 0)
    fcntl.ioctl(terminal, termios.TIOCSWINSZ, size)
    env = {k: v for k, v in os.environ.items() if k not in ("COLUMNS", "LINES")}
    demo = subprocess.Popen(
        [sys.executable, "-m", "sightglass", "--demo", "launch", "--terminal"],
        stdin=terminal,
        stdout=terminal,
        stderr=terminal,
        cwd=DOCS,  # where its sightglass.log goes (removed afterwards)
        env={**env, "TERM": "xterm-256color", "PYTHONUNBUFFERED": "1"},
        start_new_session=True,
    )
    os.close(terminal)
    started = time.monotonic()
    printed = bytearray()

    def read() -> None:  # everything the demo prints to its terminal
        while True:
            try:
                chunk = os.read(controller, 65536)
            except OSError:  # it closed the terminal
                return
            if not chunk:
                return
            printed.extend(chunk)

    threading.Thread(target=read, daemon=True).start()
    try:
        while b"Dashboard: " not in printed:
            time.sleep(0.1)
        url = printed.split(b"Dashboard: ")[1].split()[0].decode()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(
                viewport={"width": 1440, "height": 900}, device_scale_factor=2
            )
            page.goto(url)
            time.sleep(MOMENT - (time.monotonic() - started))
            page.screenshot(path=DOCS / "launch-control.png")
            # The latest whole frame: each one starts by moving the cursor home.
            frame = printed.decode("utf-8", "replace").split("\x1b[H")[-2]
            page.keyboard.press("Backquote")  # the signals view
            view = page.locator("sightglass-signals")
            view.locator("tbody tr").first.wait_for()
            page.wait_for_timeout(300)  # its charts drawn
            page.screenshot(path=DOCS / "signals-view.png", animations="disabled")
            page.keyboard.press("Backquote")
            view.locator("dialog").wait_for(state="hidden")
            # The same moment in the terminal theme (its switch, as a viewer would).
            page.locator("[data-theme-switch]").click()
            # Its bigger type makes the page taller: show all of it, in a
            # window that tall, so the CRT overlay covers it all.
            height = page.evaluate("document.documentElement.scrollHeight")
            page.set_viewport_size({"width": 1440, "height": height})
            page.wait_for_timeout(300)
            themed = DOCS / "launch-terminal-theme.png"
            # Power-on finished, the refresh roll and grain at their first frame:
            # the same picture every time.
            page.screenshot(path=themed, animations="disabled")
            # Scanlines and glow, all one green: 256 colours lose nothing visible
            # and halve the file.
            Image.open(themed).convert("RGB").quantize(256).save(themed, optimize=True)

            view = browser.new_page(viewport={"width": 2400, "height": 1600})
            html = TERMINAL.replace("COLUMNS", str(COLUMNS)).replace(
                "LINES", str(LINES)
            )
            view.set_content(html.replace("FRAME", json.dumps(frame)))
            view.wait_for_function("window.drawn === true")
            view.locator("#t").screenshot(path=DOCS / "launch-terminal.png")
            browser.close()
    finally:
        os.killpg(demo.pid, signal.SIGINT)  # Ctrl+C
        demo.wait(10)
        os.close(controller)
        (DOCS / "sightglass.log").unlink(missing_ok=True)
    print(
        "Saved launch-control.png, signals-view.png, launch-terminal-theme.png, "
        "launch-terminal.png"
    )


def plot() -> None:
    """`sightglass --demo plot` once its last minute is full: plot-demo.png."""
    demo = subprocess.Popen(
        [sys.executable, "-m", "sightglass", "--demo", "plot", "--port", "0"],
        stdin=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        url = demo.stderr.readline().split()[1]  # "Dashboard: <url>"
        time.sleep(62)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(
                viewport={"width": 1440, "height": 1300}, device_scale_factor=2
            )
            page.goto(url)
            for i in range(2):
                page.locator("[data-plot]").nth(i).wait_for()
            page.wait_for_function(
                "document.querySelectorAll('[data-plot-signals=\"100\"]').length == 2"
            )
            page.wait_for_timeout(500)
            shot = DOCS / "plot-demo.png"
            page.screenshot(path=shot, full_page=True)
            Image.open(shot).convert("RGB").quantize(256).save(shot, optimize=True)
            browser.close()
    finally:
        demo.terminate()
        demo.communicate(timeout=10)
    print("Saved plot-demo.png")


def load() -> None:
    """`sightglass --demo load` 90 s in, a glitch or two on its heat map:
    load-demo.png. Needs port 48898 free (no other fake PLC or ADS router)."""
    demo = subprocess.Popen(
        [sys.executable, "-m", "sightglass", "--demo", "load", "--port", "0"],
        stdin=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        url = demo.stderr.readline().split()[1]  # "Dashboard: <url>"
        time.sleep(90)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(
                viewport={"width": 1440, "height": 1240}, device_scale_factor=2
            )
            page.goto(url)
            page.locator(
                '[data-plot-mode="heatmap"][data-plot-signals="1000"]'
            ).wait_for()
            page.wait_for_timeout(2500)  # the readings, a second or two of them
            shot = DOCS / "load-demo.png"
            page.screenshot(path=shot, full_page=True)
            Image.open(shot).convert("RGB").quantize(256).save(shot, optimize=True)
            browser.close()
    finally:
        demo.terminate()
        demo.communicate(timeout=10)
    print("Saved load-demo.png")


if __name__ == "__main__":
    if sys.argv[1:] == ["plot"]:
        plot()
    elif sys.argv[1:] == ["load"]:
        load()
    else:
        main()
        plot()
        load()
