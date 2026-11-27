"""Dashboards described by a config file: sightglass --config."""

import asyncio
import re
import time
import urllib.request

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from sightglass import Monitor, RestSource, WebDashboard
from sightglass.cli import build, parser
from sightglass.dashboard import load, render

from .conftest import run_cli, stop, until

MINIMAL = """
[[source]]
url = "http://127.0.0.1:1/x"

[[panel]]
ids = ["a.*"]
"""


def write(tmp_path, text):
    path = tmp_path / "dashboard.toml"
    path.write_text(text, "utf-8")
    return path


def test_a_minimal_config_gets_the_defaults(tmp_path):
    dashboard = load(write(tmp_path, MINIMAL))
    # The same defaults as a dashboard made in Python or on the command line.
    assert dashboard.title == WebDashboard().title == "Dashboard"
    assert dashboard.max_elements == Monitor().max_elements == 2000
    assert dashboard.panel_width == 520
    assert dashboard.history == 360  # the longest range, and a minute
    (panel,) = dashboard.panels
    assert [(p.role, p.seconds, p.grow) for p in panel.plots] == [
        ("secondary", 300, 0.5),
        ("primary", 60, 1),
    ]
    assert panel.mode == "lines"
    (source,) = dashboard.sources
    assert isinstance(source, RestSource) and source.interval == 1.0


def test_each_makes_a_panel_per_name(tmp_path):
    text = (
        MINIMAL.split("[[panel]]")[0]
        + """
[[panel]]
each = ["North", "South"]
title = "Array {name}"
ids = ["grid.{name}.value.0.*", "grid.{name}.level"]
range = 60
secondary_range = 600
mode = "heatmap"
"""
    )
    dashboard = load(write(tmp_path, text))
    assert [p.title for p in dashboard.panels] == ["Array North", "Array South"]
    assert dashboard.panels[1].ids == ("grid.South.value.0.*", "grid.South.level")
    assert [p.seconds for p in dashboard.panels[0].plots] == [600, 60]
    assert dashboard.panels[0].mode == "heatmap"
    assert dashboard.history == 660


def test_a_source_carries_every_rest_option(tmp_path):
    text = """
[[source]]
type = "rest"
url = "http://127.0.0.1:1/x"
prefix = "p"
select = "data.items"
key = "name"
interval = 0.25
timeout = 2
accept = [503]
headers = { X-Api-Key = "abc" }

[[panel]]
ids = ["p.*"]
"""
    (source,) = load(write(tmp_path, text)).sources
    (endpoint,) = source.endpoints
    assert (endpoint.prefix, endpoint.select, endpoint.key) == (
        "p",
        "data.items",
        "name",
    )
    assert endpoint.accept == (503,) and endpoint.interval == 0.25
    assert source.interval == 0.25 and source.timeout == 2
    assert source.headers == {"X-Api-Key": "abc"}


def test_environment_variables_fill_in_a_source_so_secrets_stay_out_of_the_file(
    tmp_path, monkeypatch
):
    text = MINIMAL.replace(
        'url = "http://127.0.0.1:1/x"',
        'url = "${HOST_URL:-http://fallback}/x"\n'
        'headers = { Authorization = "Bearer ${TOKEN}" }',
    )
    path = write(tmp_path, text)
    with pytest.raises(ValueError, match="TOKEN is not set") as error:
        load(path)
    assert "Bearer" not in str(error.value)
    monkeypatch.setenv("TOKEN", "s3cret")
    (source,) = load(path).sources
    assert source.endpoints[0].url == "http://fallback/x"
    assert source.headers == {"Authorization": "Bearer s3cret"}
    monkeypatch.setenv("HOST_URL", "http://real")
    assert load(path).sources[0].endpoints[0].url == "http://real/x"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("this is not toml", "dashboard.toml"),
        (MINIMAL + "\ncolour = 1", "unknown 'colour'"),
        (
            MINIMAL.replace('ids = ["a.*"]', 'ids = ["a.*"]\nrnge = [60]'),
            "unknown 'rnge'",
        ),
        ('[[source]]\nurl = "http://x"', "at least one .*panel"),
        (MINIMAL.replace('ids = ["a.*"]', ""), "needs ids"),
        (MINIMAL.replace('ids = ["a.*"]', "ids = []"), "needs ids"),
        (MINIMAL + "range = 0", "range in .* above 0"),
        (MINIMAL + 'secondary_range = "5 min"', "secondary_range in"),
        (MINIMAL + "ranges = [300, 60]", "unknown .ranges."),
        (MINIMAL + 'mode = "pie"', "mode"),
        (MINIMAL + "each = []", "each"),
        (MINIMAL.replace('url = "http://127.0.0.1:1/x"', ""), "needs a url"),
        (
            MINIMAL.replace("[[source]]", '[[source]]\ntype = "ftp"'),
            "type must be rest",
        ),
        (MINIMAL.replace("[[source]]", "[[source]]\ninterval = -1"), "interval"),
        ("history = 10\n" + MINIMAL, "history is 10 s but a panel plots 300 s"),
        ("title = 5\n" + MINIMAL, "title and version must be text"),
    ],
)
def test_a_bad_config_says_what_is_wrong_in_one_line(tmp_path, text, message):
    with pytest.raises(ValueError, match=message) as error:
        load(write(tmp_path, text))
    assert "\n" not in str(error.value)


def test_the_page_has_an_article_per_panel_with_its_plots(tmp_path):
    text = (
        'title = "A & B"\n'
        + MINIMAL.replace("a.*", 'a.*\\" onload=\\"x')
        + 'title = "<b>"\n'
    )
    page = load(write(tmp_path, text)).page()
    assert "<title>A &amp; B</title>" in page and "<h3>&lt;b&gt;</h3>" in page
    assert 'onload="x' not in page  # a quote in an id can't open an attribute
    assert page.count("<article") == 1 and page.count('class="plot ') == 2
    assert 'data-seconds="300"' in page and 'data-seconds="60"' in page
    assert 'class="plot secondary" style="--grow: 0.5"' in page
    assert 'class="plot primary" style="--grow: 1"' in page
    assert page.index("secondary") < page.index("primary")  # secondary on top
    assert "/_sightglass/dashboard.css" in page and "sightglass.js" in page


def test_the_page_says_which_instance_it_is(tmp_path):
    from sightglass import __version__

    path = write(tmp_path, 'title = "Plant"\nversion = "2.1 <beta>"\n' + MINIMAL)
    dashboard = load(path)
    assert dashboard.config == "dashboard.toml" and dashboard.version == "2.1 <beta>"
    assert re.fullmatch(r"[0-9a-f]{8}", dashboard.digest)
    page = dashboard.page()
    identity = re.search(r'<small class="identity">(.*?)</small>', page).group(1)
    assert identity.startswith("v2.1 &lt;beta&gt; \u00b7 dashboard.toml \u00b7 config ")
    assert f"sightglass {__version__}" in identity
    assert re.search(r"started \d{4}-\d\d-\d\d \d\d:\d\d$", identity)
    # the fingerprint tells two instances with different files apart
    other = write(tmp_path, 'title = "Plant"\n' + MINIMAL)
    assert load(other).digest != dashboard.digest
    assert "v2.1" not in load(other).page()  # no version, none shown


def test_heatmap_panels_say_so(tmp_path):
    page = load(write(tmp_path, MINIMAL + 'mode = "heatmap"')).page()
    assert page.count('data-plot-mode="heatmap"') == 2
    assert render(load(write(tmp_path, MINIMAL))).count("heatmap") == 0


def test_the_cli_builds_the_dashboard_from_the_file(tmp_path):
    path = write(tmp_path, 'title = "Plant"\n' + MINIMAL)
    monitor, sources, outputs = build(parser().parse_args(["--config", str(path)]))
    web_output = outputs[0]
    assert monitor.max_elements == 2000 and monitor.history is not None
    assert "<title>Plant</title>" in web_output.html and web_output.title == "Plant"
    assert len(sources) == 1 and isinstance(sources[0], RestSource)


PANELS_ONLY = """
title = "App"
[[panel]]
ids = ["app.cpu"]
range = 60
"""


def test_a_file_of_panels_alone_is_fed_by_post_update(tmp_path, capsys):
    path = write(tmp_path, PANELS_ONLY)
    assert load(path).sources == []
    _, sources, (web_output,) = build(
        parser().parse_args(["--config", str(path), "--port", "0"])
    )
    assert sources == [] and "<title>App</title>" in web_output.html
    assert "POST /update" in capsys.readouterr().err  # says where values come from


def test_python_serves_a_config_dashboard_and_feeds_it(tmp_path, capsys):
    monitor = load(write(tmp_path, PANELS_ONLY)).start(port=0)
    try:
        (url,) = re.findall(r"http://\S+", capsys.readouterr().err)

        def get(route):
            with urllib.request.urlopen(url + route, timeout=5) as response:
                return response.read().decode()

        assert 'data-plot="app.cpu"' in get("")
        monitor.set("app.cpu", 12.5)
        deadline = time.monotonic() + 3
        while "12.5" not in get("history?id=app.cpu&seconds=5"):
            assert time.monotonic() < deadline, "the value never reached the history"
            time.sleep(0.05)
    finally:
        monitor.stop()


def test_the_where_of_a_config_dashboard_comes_from_the_caller(tmp_path):
    dashboard = load(write(tmp_path, 'theme = "terminal"\n' + PANELS_ONLY))
    web_output = dashboard.web(port=0, token="t", debug=True, announce=False)
    assert (web_output.port, web_output.token, web_output.debug) == (0, "t", True)
    assert (web_output.theme, web_output.title) == ("terminal", "App")
    monitor = dashboard.monitor()
    assert monitor.history is not None
    assert monitor.history.seconds == dashboard.history == 60 + 300  # + secondary


def test_a_config_dashboard_keeps_history_only_for_what_it_plots(tmp_path):
    text = """
[[source]]
url = "http://127.0.0.1:1/x"
[[panel]]
each = ["N"]
ids = ["p.{name}.value.0.*", "p.level"]
"""
    path = write(tmp_path, text)
    assert load(path).plotted() == ["p.N.value.0.*", "p.level"]
    monitor, _, _ = build(parser().parse_args(["--config", str(path)]))
    monitor.update(
        {
            "p.N.value.0.1": 1.0,
            "p.N.value.1.1": 2.0,
            "p.N.flags.0.1": 3.0,
            "p.level": 4.0,
        }
    )
    assert sorted(monitor.history._tracks) == ["p.N.value.0.1", "p.level"]


@pytest.mark.parametrize(
    "argv",
    [
        ["--rest", "http://x"],
        ["--serial", "auto"],
        ["--title", "T"],
        ["--history", "5"],
        ["--max-elements", "9"],
        ["--terminal"],
        ["--demo"],
        ["--rest-interval", "2"],
        ["--theme", "terminal"],
        ["--stale-after", "2"],
    ],
)
def test_the_cli_refuses_options_the_file_replaces(tmp_path, argv):
    path = write(tmp_path, MINIMAL)
    with pytest.raises(ValueError, match="--config describes the whole dashboard"):
        build(parser().parse_args(["--config", str(path), *argv]))


async def test_a_dashboard_from_a_file_serves_its_page_and_shows_the_values(
    tmp_path, monkeypatch
):
    async def readings(request):
        return web.json_response(
            {"sensors": {"N": {"value": [[1.5, 2.5], [3.5, None]]}}}
        )

    app = web.Application()
    app.router.add_get("/api", readings)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setenv("API_URL", str(server.make_url("/api")))
    text = """
title = "Plant"
[[source]]
url = "${API_URL}"
prefix = "p"
select = "sensors"
interval = 0.05
[[panel]]
each = ["N"]
ids = ["p.{name}.value.*"]
"""
    path = write(tmp_path, text)
    argv = ["--config", str(path), "--port", "0"]
    monitor, sources, outputs = build(parser().parse_args(argv))
    outputs[0].announce = False
    monitor.start(sources=sources, outputs=outputs)
    try:
        await until(lambda: "p.N.value.1.0" in monitor)
        assert "p.N.value.1.1" not in monitor  # null: no display
        base = outputs[0].url

        def get(route):
            with urllib.request.urlopen(base + route, timeout=5) as response:
                return response.read().decode()

        assert 'data-plot="p.N.value.*"' in get("")
        assert ".secondary" in get("_sightglass/dashboard.css")
        assert '"p.N.value.0.1"' in get("values")
    finally:
        monitor.stop()
        await server.close()


def test_the_file_says_what_the_dashboard_is_the_cli_where_it_runs(tmp_path):
    path = write(tmp_path, 'theme = "terminal"\nstale_after = 3\n' + MINIMAL)
    argv = ["--config", str(path), "--port", "0", "--token", "t", "--debug"]
    _, _, (web_output, _audit) = build(parser().parse_args(argv))
    assert (web_output.theme, web_output.stale_after) == ("terminal", 3)
    assert (web_output.port, web_output.token, web_output.debug) == (0, "t", True)


@pytest.mark.parametrize(
    ("setting", "error"),
    [
        ('theme = "neon"', "theme must be classic or terminal"),
        ("stale_after = 0", "stale_after in the config must be a number above 0"),
        ("debug = true", "unknown 'debug'"),  # how it runs: --debug
    ],
)
def test_the_files_display_settings_are_checked(tmp_path, setting, error):
    with pytest.raises(ValueError, match=error):
        load(write(tmp_path, setting + "\n" + MINIMAL))


async def test_a_panel_whose_id_matches_nothing_is_warned_about_once(tmp_path):
    async def readings(request):
        return web.json_response({"res": {"host": {"cpu_percent": 3.5, "mem": 40}}})

    app = web.Application()
    app.router.add_get("/api", readings)
    server = TestServer(app)
    await server.start_server()
    text = f"""
[[source]]
url = "{server.make_url("/api")}"
interval = 0.05
[[section]]
title = "Host"
[[section.panel]]
type = "value"
title = "CPU"
id = "res.cpu_percent"
[[section.panel]]
type = "value"
title = "Memory"
id = "res.host.mem"
[[section.panel]]
title = "History"
ids = ["res.host.*"]
"""
    process, _ = run_cli("--config", str(write(tmp_path, text)))
    try:
        await asyncio.sleep(1.5)  # several polls: the warning must come once
    finally:
        _, err = stop(process)
        await server.close()
    assert err.count("no id") == 1
    assert (
        """panel "CPU" (section "Host"): no id 'res.cpu_percent'; """
        "did you mean 'res.host.cpu_percent'?" in err
    )
