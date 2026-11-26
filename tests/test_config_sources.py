"""Every kind of source in a config file, and the InfluxDB source they add."""

import logging

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from sightglass import (
    BinaryFrameDecoder,
    CsvDecoder,
    InfluxSource,
    JsonDecoder,
    KeyValueDecoder,
    Monitor,
    SerialSource,
    ZmqSource,
)
from sightglass.dashboard import load
from sightglass.sources.influx import flux, rows

from .conftest import until

PANEL = """
[[panel]]
ids = ["a.*"]
"""


def sources(tmp_path, source, panel=PANEL):
    path = tmp_path / "dashboard.toml"
    path.write_text(f"[[source]]\n{source}\n{panel}", "utf-8")
    return load(path).sources


# --- the config file ---------------------------------------------------------


def test_an_ads_source_is_described_in_the_file(tmp_path):
    from sightglass import AdsSource

    (source,) = sources(
        tmp_path,
        """type = "ads"
target = "5.12.34.56.1.1:851"
variables = ["MAIN.*", "GVL.fTemperature"]
interval = 0.05
ip = "10.0.0.5"
""",
    )
    assert isinstance(source, AdsSource)
    assert (source.target, source.interval, source.ip_address) == (
        "5.12.34.56.1.1:851",
        0.05,
        "10.0.0.5",
    )
    assert [v.name for v in source.variables] == ["MAIN.*", "GVL.fTemperature"]
    assert not source.allow_writes  # a dashboard file never writes to a PLC


def test_an_ads_source_can_use_an_interface_file_beside_the_config(tmp_path):
    (tmp_path / "plc.toml").write_text(
        'target = "5.12.34.56.1.1"\n[variables]\n"GVL.fTemperature" = {}\n', "utf-8"
    )
    (source,) = sources(tmp_path, 'type = "ads"\nfile = "plc.toml"\ninterval = 0.5')
    assert source.target == "5.12.34.56.1.1" and source.interval == 0.5


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ('type = "ads"', "needs a target"),
        ('type = "ads"\ntarget = "5.12.34.56.1.1"', "variables .* must be names"),
        (
            'type = "ads"\nfile = "plc.toml"\ntarget = "5.12.34.56.1.1"',
            "described in the file",
        ),
        ('type = "ads"\nfile = "missing.toml"', "missing.toml"),
        ('type = "ads"\ntarget = "x:y"\nvariables = ["A.*"]', "expected an AMS"),
        ('type = "zmq"\npattern = "pub"', "pattern .* sub or pull"),
        ('type = "zmq"\nfields = ["a"]', 'goes with decoder = "csv"'),
        ('type = "zmq"\ndecoder = "csv"', "needs fields"),
        ('type = "serial"\ndecoder = "binary"', "needs bytes"),
        ('type = "serial"\ndecoder = "xml"', "decoder .* must be"),
        ('type = "serial"\nbaudrate = 0', "baudrate"),
        ('type = "influxdb"', "needs a url"),
        (
            'type = "influxdb"\nurl = "http://x"\norg = "o"\ntoken = "t"',
            "bucket or a q",
        ),
        (
            'type = "influxdb"\nurl = "http://x"\norg = "o"\ntoken = "t"\n'
            'bucket = "b"\nquery = "q"',
            "bucket or a query, and not both",
        ),
        (
            'type = "influxdb"\nurl = "http://x"\norg = "o"\ntoken = "t"\n'
            'bucket = "b"\ntags = { a = 1 }',
            "tags .* table of text",
        ),
        ('type = "ads"\nurl = "http://x"', "unknown 'url'"),
        ('type = "rest"\norg = "o"', "unknown 'org'"),
    ],
)
def test_a_bad_source_says_what_is_wrong_in_one_line(tmp_path, source, message):
    with pytest.raises(ValueError, match=message) as error:
        sources(tmp_path, source)
    assert "\n" not in str(error.value)


def test_zmq_and_serial_sources_take_a_decoder(tmp_path):
    zmq, serial, binary = sources(
        tmp_path,
        'type = "zmq"\nendpoint = "tcp://127.0.0.1:5599"\npattern = "pull"\n'
        'decoder = "json"\n\n[[source]]\ntype = "serial"\nport = "COM3"\n'
        'baudrate = 9600\ndecoder = "csv"\nfields = ["a", "b"]\n\n[[source]]\n'
        'type = "serial"\ndecoder = "binary"\nbytes = { "1" = "X", "2" = "Y" }',
    )
    assert isinstance(zmq, ZmqSource) and zmq.pattern == "pull" and zmq.bind
    assert isinstance(zmq.decoder, JsonDecoder)
    assert isinstance(serial, SerialSource)
    assert (serial.port, serial.baudrate) == ("COM3", 9600)
    assert isinstance(serial.decoder, CsvDecoder)
    assert isinstance(binary.decoder, BinaryFrameDecoder)
    (default,) = sources(tmp_path, 'type = "zmq"')
    assert isinstance(default.decoder, KeyValueDecoder) and default.pattern == "sub"


def test_a_token_in_an_influxdb_source_can_come_from_the_environment(
    tmp_path, monkeypatch
):
    text = (
        'type = "influxdb"\nurl = "http://127.0.0.1:1"\norg = "gf"\n'
        'token = "${INFLUX_TOKEN}"\nbucket = "plant"\nmeasurement = "cell"\n'
        'fields = ["v1", "v2"]\ntags = { site = "north" }\nwindow = 30\n'
        'id_from = ["_measurement", "unit", "_field"]\nprefix = "p"\ninterval = 0.5'
    )
    with pytest.raises(ValueError, match="INFLUX_TOKEN is not set"):
        sources(tmp_path, text)
    monkeypatch.setenv("INFLUX_TOKEN", "s3cret")
    (source,) = sources(tmp_path, text)
    assert isinstance(source, InfluxSource)
    assert source.token == "s3cret" and source.interval == 0.5
    assert source.id_from == ("_measurement", "unit", "_field")
    assert "-30000ms" in source.query and '"north"' in source.query


# --- the InfluxDB source ------------------------------------------------------

TABLES = (
    "#group,false,false,true,true,false,false,true,true\r\n"
    "#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,"
    "double,string,string\r\n"
    "#default,_result,,,,,,,\r\n"
    ",result,table,_start,_stop,_time,_value,_field,_measurement\r\n"
    ",,0,2026-01-01T00:00:00Z,2026-01-01T00:01:00Z,2026-01-01T00:00:59Z,1.5,v1,cell\r\n"
    "\r\n"
    "#group,false,false,true,true,false,false,true,true\r\n"
    "#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,"
    "long,string,string\r\n"
    "#default,_result,,,,,,,\r\n"
    ",result,table,_start,_stop,_time,_value,_field,_measurement\r\n"
    ",,1,2026-01-01T00:00:00Z,2026-01-01T00:01:00Z,2026-01-01T00:00:58Z,7,count,cell\r\n"
    "\r\n"
    "#datatype,string,long,dateTime:RFC3339,boolean,string,string\r\n"
    ",result,table,_time,_value,_field,_measurement\r\n"
    ",,2,2026-01-01T00:00:58Z,true,ok,cell\r\n"
    ",,2,2026-01-01T00:00:58Z,,idle,cell\r\n"
    "\r\n"
)


def test_flux_csv_becomes_ids_and_typed_values():
    assert rows(TABLES, ("_measurement", "_field")) == {
        "cell.v1": 1.5,
        "cell.count": 7,
        "cell.ok": True,
        "cell.idle": None,  # an empty value is invalid
    }
    assert rows(TABLES, ("_field",), prefix="p")["p.v1"] == 1.5


def test_a_flux_error_in_the_reply_is_a_failure():
    from sightglass.sources._poll import Failed

    with pytest.raises(Failed, match="the query failed: bad flux"):
        rows("error,reference\r\nbad flux,897\r\n", ("_field",))


def test_the_query_names_only_what_was_asked_for_and_quotes_it():
    query = flux("plant", "cell", ["v1", "v2"], {"site": "north"}, 60)
    assert 'from(bucket: "plant")' in query
    assert "range(start: -60000ms)" in query
    assert 'r._field == "v1" or r._field == "v2"' in query
    assert 'r["site"] == "north"' in query
    assert query.endswith("|> last()")
    hostile = flux('a"b', "${x}\\", [], {}, 1)
    assert 'bucket: "a\\"b"' in hostile and '"\\${x}\\\\"' in hostile


def test_a_source_needs_a_bucket_or_a_query():
    with pytest.raises(ValueError, match="bucket, or a query"):
        InfluxSource("http://x", org="o", token="t")
    with pytest.raises(ValueError, match="bucket, or a query"):
        InfluxSource("http://x", org="o", token="t", bucket="b", query="q")


async def influx_server(handler):
    app = web.Application()
    app.router.add_post("/api/v2/query", handler)
    server = TestServer(app)
    await server.start_server()
    return server


async def test_influxdb_values_reach_the_dashboard_and_go_blank_when_they_stop(
    consume,
):
    seen = {}
    replies = [TABLES]

    async def query(request):
        seen["auth"] = request.headers["Authorization"]
        seen["org"] = request.query["org"]
        seen["flux"] = (await request.read()).decode()
        return web.Response(
            text=replies[0], content_type="application/csv", charset="utf-8"
        )

    server = await influx_server(query)
    try:
        monitor = Monitor()
        source = InfluxSource(
            str(server.make_url("")),
            org="gf",
            token="s3cret",
            bucket="plant",
            interval=0.02,
        )
        consume(monitor, source)
        await until(lambda: "cell.v1" in monitor)
        assert seen["auth"] == "Token s3cret" and seen["org"] == "gf"
        assert 'from(bucket: "plant")' in seen["flux"]
        assert monitor["cell.v1"].value == 1.5
        assert "cell.idle" not in monitor  # never valid: no display
        replies[0] = ""  # the series stop having points in the window
        await until(lambda: monitor["cell.v1"].value == "")
        assert monitor["cell.ok"].value is False  # a lamp goes off
        replies[0] = TABLES.replace(",1.5,", ",2.5,")
        await until(lambda: monitor["cell.v1"].value == 2.5)
    finally:
        await server.close()


async def test_an_influxdb_that_rejects_the_token_is_logged_once_and_blanks_the_values(
    consume, caplog
):
    ok = [True]
    calls = []

    async def query(request):
        calls.append(1)
        if ok[0]:
            return web.Response(text=TABLES, content_type="application/csv")
        return web.json_response(
            {"code": "unauthorized", "message": "unauthorized access"}, status=401
        )

    server = await influx_server(query)
    try:
        monitor = Monitor()
        consume(
            monitor,
            InfluxSource(
                str(server.make_url("")),
                org="gf",
                token="bad",
                bucket="plant",
                interval=0.02,
            ),
        )
        await until(lambda: "cell.v1" in monitor)
        with caplog.at_level(logging.WARNING):
            ok[0] = False
            await until(lambda: monitor["cell.v1"].value == "")
            seen = len(calls)
            await until(lambda: len(calls) > seen + 3)
    finally:
        await server.close()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "HTTP 401 unauthorized access" in warnings[0]
    assert "s3cret" not in warnings[0] and "bad" not in warnings[0]


def test_a_dollar_brace_in_a_flux_query_can_be_escaped(tmp_path, monkeypatch):
    """Flux has its own ${...}: $${ writes it without asking for a variable."""
    monkeypatch.setenv("BUCKET", "plant")
    (source,) = sources(
        tmp_path,
        'type = "influxdb"\nurl = "http://x"\norg = "o"\ntoken = "t"\n'
        'query = """from(bucket: "${BUCKET}") |> map(fn: (r) => '
        '({r with s: "$${r._field}"}))"""',
    )
    assert source.query == (
        'from(bucket: "plant") |> map(fn: (r) => ({r with s: "${r._field}"}))'
    )
