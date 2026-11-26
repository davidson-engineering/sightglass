"""``sightglass``: show live values without writing any code.

my_program | sightglass                     # key=value or JSON lines on stdout
sightglass                                  # then: curl -d 'temp=21.5' .../update
sightglass --serial auto --csv temp,humidity
sightglass --zmq tcp://localhost:5556
sightglass --ads 5.12.34.56.1.1 --vars 'MAIN.*'
sightglass --rest http://localhost:8000/metrics
sightglass --config dashboard.toml
sightglass --demo
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Sequence
from importlib import resources

from . import __version__
from .decoders import (
    BinaryFrameDecoder,
    CsvDecoder,
    Decoder,
    JsonDecoder,
    KeyValueDecoder,
)
from .history import History
from .monitor import WRITES_ENV, Monitor, Output, Source
from .sources.stdin import StdinSource

EXAMPLES = """\
examples:
  my_program | sightglass               pipe a program's output: lines like
                                         "progress=5 status=running" or JSON
                                         objects become values; other lines are
                                         printed as usual
  sightglass                            then POST values from anything:
                                         curl -d temp=21.5 localhost:8080/update
  sightglass --serial auto --csv temperature,humidity
  sightglass --serial /dev/ttyUSB0 --binary 1=X,2=Y,10=velocity
  sightglass --zmq tcp://localhost:5556
  sightglass --zmq tcp://*:5557 --pull  receive from PUSH sockets (none are lost)
  sightglass --ads 5.12.34.56.1.1 --vars 'MAIN.*,GVL.fTemperature'
                                        a Beckhoff PLC over ADS
  sightglass --ads plc.toml             the PLC, variables and types in a file
  sightglass --rest http://localhost:8000/metrics --rest-interval 2
                                        poll a JSON REST endpoint (repeat --rest
                                        for more); ids start with its last path
                                        segment: metrics.process.memory_mb
  sightglass --config dashboard.toml    a dashboard described by a file: where
                                        the values come from and what to plot
  sightglass --demo                     simulated data, every kind of element
  sightglass --demo launch --open       the showcase: a rocket launch, live
  sightglass --demo launch --terminal   ... drawn in the terminal too
  sightglass --demo plot --open         100 waveforms at 20 Hz, plotted against time
  sightglass --demo load --open         a fake PLC's 1000 signals over ADS, measured
"""


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sightglass",
        description="Live dashboard for values from your program, a device, or "
        "anything that can make an HTTP request.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--guide",
        action="store_true",
        help="print a concise guide to building monitors (written for AI coding "
        "agents) and exit",
    )

    inputs = p.add_argument_group(
        "inputs (stdin is read when it's a pipe; the dashboard also accepts "
        "POST /update)"
    )
    inputs.add_argument("--serial", metavar="PORT", help='serial device, or "auto"')
    inputs.add_argument(
        "--baudrate", type=int, default=115200, help="(default: 115200)"
    )
    serial_format = inputs.add_mutually_exclusive_group()
    serial_format.add_argument(
        "--csv",
        metavar="NAMES",
        help="device sends comma-separated values, in this order",
    )
    serial_format.add_argument(
        "--json", action="store_true", help="device sends JSON objects, one per line"
    )
    serial_format.add_argument(
        "--binary",
        metavar="MAP",
        help="device sends binary frames; MAP names the id bytes, e.g. 1=X,10=velocity",
    )
    inputs.add_argument("--zmq", metavar="ENDPOINT", help="receive ZeroMQ messages")
    inputs.add_argument(
        "--pull",
        action="store_true",
        help="with --zmq: bind a PULL socket instead of SUB",
    )
    inputs.add_argument(
        "--ads",
        metavar="PLC",
        help="a Beckhoff TwinCAT PLC over ADS: its AMS net id or address, "
        "optionally :port (default 851), or an interface file (.toml)",
    )
    inputs.add_argument(
        "--vars",
        metavar="NAMES",
        help="with --ads: PLC variables to show, comma-separated; * matches any "
        "characters, e.g. 'MAIN.*,GVL.fTemperature'",
    )
    inputs.add_argument(
        "--allow-writes",
        action="store_true",
        help="with an --ads interface file: write values sent to variables marked "
        f"write = true to the PLC (needs --token, and {WRITES_ENV}=1 in the "
        "environment)",
    )
    inputs.add_argument(
        "--rest",
        metavar="URL",
        action="append",
        help="poll a URL that answers GET with JSON; repeat for several. Ids are "
        "the JSON's names, dotted, after the URL's last path segment",
    )
    inputs.add_argument(
        "--rest-interval",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="with --rest: time between polls (default: 1)",
    )
    inputs.add_argument(
        "--rest-header",
        metavar="'NAME: VALUE'",
        action="append",
        help="with --rest: send this header with every request, e.g. an API key "
        "(repeatable)",
    )
    inputs.add_argument(
        "--config",
        metavar="FILE",
        help="a dashboard described by a TOML file: its sources, and the "
        "panels that show them (see the README); replaces the other inputs",
    )
    inputs.add_argument(
        "--demo",
        nargs="?",
        const="elements",
        choices=["elements", "launch", "plot", "load"],
        metavar="launch|plot|load",
        help="show simulated data: every kind of element on the generic page; "
        "with 'launch', a rocket launch on a hand-built page, fed by three "
        "programs; with 'plot', 100 waveforms at 20 Hz on value-vs-time plots; "
        "with 'load', 1000 signals of a fake PLC read over ADS at 20 Hz, and what "
        "that costs (needs the ads extra)",
    )

    display = p.add_argument_group("display")
    display.add_argument("--port", type=int, default=8080, help="(default: 8080)")
    display.add_argument(
        "--host",
        default="127.0.0.1",
        help="interface to listen on; 0.0.0.0 allows other machines",
    )
    display.add_argument("--title", default="Monitor", help="page heading")
    display.add_argument(
        "--theme",
        choices=["classic", "terminal"],
        default="classic",
        help="the page's look unless a viewer switches (default: classic)",
    )
    display.add_argument(
        "--open", action="store_true", help="open the dashboard in a browser"
    )
    display.add_argument("--token", help="require this token to POST /update")
    display.add_argument(
        "--stale-after",
        type=float,
        metavar="SECONDS",
        help="dim values when no data has arrived for this long",
    )
    display.add_argument(
        "--max-elements",
        type=int,
        default=500,
        metavar="N",
        help="the most ids that get a display of their own (default: 500); "
        "raise it for a PLC with more variables",
    )
    display.add_argument(
        "--history",
        type=float,
        metavar="SECONDS",
        help="keep this long of every numeric value for plots (GET /history)",
    )
    display.add_argument(
        "--terminal",
        action="store_true",
        help="draw in this terminal instead of serving a web page "
        "(log messages go to sightglass.log)",
    )
    display.add_argument("-v", "--verbose", action="store_true", help="log more detail")
    return p


def build(args: argparse.Namespace) -> tuple[Monitor, list[Source], list[Output]]:
    """The monitor, sources and outputs the arguments describe."""
    if args.config:
        return config_build(args)
    sources: list[Source] = []
    if args.demo:
        from . import demo

        monitor = demo.build_monitor()
        sources.append(demo.source())
    else:
        monitor = Monitor()
    if args.max_elements < 1:
        raise ValueError("--max-elements must be at least 1")
    monitor.max_elements = args.max_elements
    if args.history is not None:
        monitor.history = History(args.history)

    if args.serial:
        from . import SerialSource

        sources.append(
            SerialSource(args.serial, args.baudrate, decoder=serial_decoder(args))
        )
    elif args.csv or args.json or args.binary:
        raise ValueError("--csv, --json and --binary describe a --serial device")

    if args.zmq:
        from . import ZmqSource

        sources.append(ZmqSource(args.zmq, pattern="pull" if args.pull else "sub"))
    elif args.pull:
        raise ValueError("--pull goes with --zmq")

    if args.ads:
        sources.append(ads_source(args))
    elif args.vars or args.allow_writes:
        raise ValueError("--vars and --allow-writes go with --ads")

    if args.rest:
        sources.append(rest_source(args))
    elif args.rest_header or args.rest_interval != 1.0:
        raise ValueError("--rest-interval and --rest-header go with --rest")

    if sys.stdin is not None and not sys.stdin.isatty() and not args.demo:
        stdin = StdinSource(echo=not args.terminal)
        stdin.on_end = lambda: _input_ended(stdin)
        sources.append(stdin)

    outputs: list[Output] = []
    if args.terminal:
        from .terminal import TerminalDisplay

        outputs.append(TerminalDisplay())
    else:
        from . import WebDashboard

        outputs.append(
            WebDashboard(
                host=args.host,
                port=args.port,
                title=args.title,
                theme=args.theme,
                token=args.token,
                stale_after=args.stale_after,
                open_browser=args.open,
            )
        )
    return monitor, sources, outputs


def config_build(
    args: argparse.Namespace,
) -> tuple[Monitor, list[Source], list[Output]]:
    """The dashboard a config file describes, on the page the file's panels make."""
    from .dashboard import load

    elsewhere = {
        "--serial": args.serial,
        "--csv": args.csv,
        "--json": args.json,
        "--binary": args.binary,
        "--zmq": args.zmq,
        "--pull": args.pull,
        "--ads": args.ads,
        "--vars": args.vars,
        "--allow-writes": args.allow_writes,
        "--rest": args.rest,
        "--rest-interval": args.rest_interval != 1.0,
        "--rest-header": args.rest_header,
        "--demo": args.demo,
        "--terminal": args.terminal,
        "--title": args.title != "Monitor",
        "--history": args.history is not None,
        "--max-elements": args.max_elements != 500,
    }
    if given := [flag for flag, value in elsewhere.items() if value]:
        raise ValueError(
            f"--config describes the whole dashboard; drop {', '.join(given)} "
            "(sources, title, history and limits are set in the file)"
        )
    from . import WebDashboard
    from .history import History

    dashboard = load(args.config)
    monitor = Monitor(max_elements=dashboard.max_elements)
    monitor.add(*dashboard.elements())  # e.g. a number shown to fixed decimals
    # Only what the panels plot: the rest of a source's ids would cost memory
    # and time (8 bytes a slot each) for plots that never draw them.
    monitor.history = History(dashboard.history, only=dashboard.plotted())
    web = WebDashboard(
        html=dashboard.page(),
        host=args.host,
        port=args.port,
        title=dashboard.title,
        theme=args.theme,
        token=args.token,
        stale_after=args.stale_after,
        open_browser=args.open,
    )
    return monitor, dashboard.sources, [web]


def launch(args: argparse.Namespace) -> None:
    """The launch demo: its own page and data, until Ctrl+C."""
    _own_page_demo(args)
    from .launch import run

    run(
        host=args.host,
        port=args.port,
        open_browser=args.open,
        terminal=args.terminal,
        theme=args.theme,
    )


def plot(args: argparse.Namespace) -> None:
    """The plot demo: its own page and data, until Ctrl+C."""
    _own_page_demo(args, terminal=False)
    from .plot import run

    run(host=args.host, port=args.port, open_browser=args.open, theme=args.theme)


def load(args: argparse.Namespace) -> None:
    """The load demo: a fake PLC read over ADS, until Ctrl+C."""
    _own_page_demo(args, terminal=False)
    from .load import run

    run(host=args.host, port=args.port, open_browser=args.open, theme=args.theme)


def _own_page_demo(args: argparse.Namespace, *, terminal: bool = True) -> None:
    """Refuse options that a demo with its own page and data would ignore."""
    ignored = {
        "--serial": args.serial,
        "--csv": args.csv,
        "--json": args.json,
        "--binary": args.binary,
        "--zmq": args.zmq,
        "--pull": args.pull,
        "--config": args.config,
        "--ads": args.ads,
        "--rest": args.rest,
        "--vars": args.vars,
        "--allow-writes": args.allow_writes,
        "--title": args.title != "Monitor",
        "--token": args.token,
        "--stale-after": args.stale_after,
        "--history": args.history is not None,
        "--max-elements": args.max_elements != 500,
        "--terminal": args.terminal and not terminal,
    }
    if given := [flag for flag, value in ignored.items() if value]:
        raise ValueError(
            f"--demo {args.demo} has its own page and data; drop {', '.join(given)}"
        )


def guide() -> str:
    """The agent guide shipped with the package (GUIDE.md)."""
    return resources.files("sightglass").joinpath("GUIDE.md").read_text("utf-8")


def _input_ended(stdin: StdinSource) -> None:
    if not stdin.lines_read:  # e.g. started with </dev/null: nothing to report
        return
    print(
        "sightglass: input ended; still showing the last values (Ctrl+C to quit)",
        file=sys.stderr,
        flush=True,
    )


def rest_source(args: argparse.Namespace) -> Source:
    from urllib.parse import urlsplit

    from . import Endpoint, RestSource

    headers = {}
    for header in args.rest_header or []:
        name, sep, value = header.partition(":")
        if not sep or not name.strip():
            raise ValueError(f"--rest-header wants 'NAME: VALUE', not {header!r}")
        headers[name.strip()] = value.strip()
    endpoints = []
    for url in args.rest:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError(f"--rest wants an http:// or https:// URL, not {url!r}")
        prefix = parts.path.rstrip("/").rpartition("/")[2]
        endpoints.append(Endpoint(url, prefix=prefix))
    return RestSource(*endpoints, interval=args.rest_interval, headers=headers)


def ads_source(args: argparse.Namespace) -> Source:
    interface_file = args.ads.endswith(".toml")
    if interface_file and args.vars:
        raise ValueError("--vars goes with a PLC address; list them in the file")
    if interface_file and args.allow_writes and not args.token:
        raise ValueError(
            "--allow-writes needs --token, so only programs that have the token "
            "can write to the PLC"
        )
    if not interface_file and args.allow_writes:
        raise ValueError(
            "--allow-writes needs an interface file marking the variables to write"
        )
    if not interface_file and not args.vars:
        raise ValueError("--ads needs --vars naming what to show, e.g. --vars 'MAIN.*'")

    from . import AdsSource  # needs pyads

    if interface_file:
        return AdsSource.from_file(args.ads, allow_writes=args.allow_writes)
    return AdsSource(args.ads, split_names(args.vars))


def split_names(text: str) -> list[str]:
    """Comma-separated PLC names; commas inside [] are array indexes."""
    return [name.strip() for name in re.split(r",(?![^\[]*\])", text) if name.strip()]


def serial_decoder(args: argparse.Namespace) -> Decoder:
    if args.csv:
        return CsvDecoder([name.strip() for name in args.csv.split(",")])
    if args.json:
        return JsonDecoder()
    if args.binary:
        names = {}
        for pair in args.binary.split(","):
            byte, sep, name = pair.partition("=")
            if not sep or not byte.strip().isdigit():
                raise ValueError(f"--binary expects BYTE=NAME pairs, got {pair!r}")
            names[int(byte)] = name.strip()
        return BinaryFrameDecoder(names)
    return KeyValueDecoder()


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.guide:
        print(guide(), end="")
        return 0
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
        # The terminal display would be drawn over any log output.
        filename="sightglass.log" if args.terminal else None,
    )
    try:
        if args.demo == "launch":
            launch(args)
        elif args.demo == "plot":
            plot(args)
        elif args.demo == "load":
            load(args)
        else:
            monitor, sources, outputs = build(args)
            monitor.serve(sources=sources, outputs=outputs)
    except (ImportError, OSError, ValueError) as error:
        print(error_message(error, args.port), file=sys.stderr)
        return 1
    return 0


def error_message(error: Exception, port: int) -> str:
    """A setup error as one line, with a free port to try if ``port`` is taken."""
    message = error.strerror if isinstance(error, OSError) else None
    message = message or str(error)
    if "already in use" in message:
        message += f", e.g. --port {port + 1}"
    return f"sightglass: error: {message}"


if __name__ == "__main__":
    sys.exit(main())
