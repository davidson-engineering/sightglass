"""The sources a config file can name: ``[[source]]`` of a ``type``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..decoders import Decoder
from ..monitor import Source
from ._config import expand, number, only

SOURCE_KEYS = {
    "rest": {
        "url",
        "prefix",
        "select",
        "key",
        "interval",
        "timeout",
        "accept",
        "include",
        "exclude",
        "headers",
        "events",
        "cursor",
        "line",
        "id",
    },
    "ads": {"file", "target", "variables", "types", "ip", "interval", "timeout"},
    "zmq": {"endpoint", "pattern", "bind", "decoder", "fields", "names"},
    "serial": {"port", "baudrate", "decoder", "fields", "names"},
    "influxdb": {
        "url",
        "org",
        "token",
        "bucket",
        "query",
        "measurement",
        "fields",
        "tags",
        "window",
        "id_from",
        "prefix",
        "interval",
        "timeout",
    },
}
SOURCE_TYPES = tuple(SOURCE_KEYS)
DECODERS = ("keyvalue", "json", "csv", "binary")


def build(table: dict[str, Any], index: int, base: Path) -> Source:
    where = f"[[source]] {index + 1}"
    kind = table.get("type", "rest")
    if kind not in SOURCE_TYPES:
        raise ValueError(
            f"{where}: type must be {' or '.join(SOURCE_TYPES)}, not {kind!r}"
        )
    only(table, SOURCE_KEYS[kind] | {"type"}, where)
    table = expand(table, where)
    return SOURCES[kind](table, where, base)


def _rest(table: dict[str, Any], where: str, base: Path) -> Source:
    if not isinstance(table.get("url"), str) or not table["url"]:
        raise ValueError(f"{where} needs a url")
    from .. import RestSource

    accept = table.get("accept", [])
    if not isinstance(accept, list) or not all(isinstance(s, int) for s in accept):
        raise ValueError(f"accept in {where} must be a list of status codes")
    patterns = {}
    for name in ("include", "exclude"):
        value = table.get(name, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{name} in {where} must be a list of id patterns")
        patterns[name] = tuple(value)
    headers = table.get("headers", {})
    if not isinstance(headers, dict) or not all(
        isinstance(v, str) for v in headers.values()
    ):
        raise ValueError(f"headers in {where} must be a table of text")
    for name in ("events", "cursor", "line", "id"):
        if not isinstance(table.get(name, ""), str):
            raise ValueError(f"{name} in {where} must be text")
    if "events" not in table and {"cursor", "line", "id"} & table.keys():
        raise ValueError(f"cursor, line and id in {where} go with events")
    try:
        endpoint = _endpoint(table, where, accept, patterns)
    except ValueError as error:
        raise ValueError(f"{where}: {error}") from None
    return RestSource(
        endpoint,
        interval=endpoint.interval or 1.0,
        headers=headers,
        timeout=number(table, "timeout", 5.0, where),
    )


def _endpoint(
    table: dict[str, Any], where: str, accept: list[int], patterns: dict[str, Any]
) -> Any:
    from .. import Endpoint

    return Endpoint(
        table["url"],
        prefix=str(table.get("prefix", "")),
        select=str(table.get("select", "")),
        key=str(table.get("key", "id")),
        interval=number(table, "interval", 1.0, where) if "interval" in table else None,
        accept=tuple(accept),
        include=patterns["include"],
        exclude=patterns["exclude"],
        events=table.get("events"),
        cursor=table.get("cursor", "id"),
        line=table.get("line", ""),
        id=table.get("id", ""),
    )


def _ads(table: dict[str, Any], where: str, base: Path) -> Source:
    """A PLC: ``file`` names an interface file (relative to this config), or
    ``target`` and ``variables`` say it here. Never writes to the PLC."""
    from .. import AdsSource

    options: dict[str, Any] = {}
    for key in ("interval", "timeout"):
        if key in table:
            options[key] = number(table, key, 1.0, where)
    if "file" in table:
        if not isinstance(table["file"], str):
            raise ValueError(f"file in {where} must be a path")
        if {"target", "variables", "types", "ip"} & table.keys():
            raise ValueError(f"{where}: with a file, the PLC is described in the file")
        try:
            return AdsSource.from_file(base / table["file"], **options)
        except OSError as error:
            raise ValueError(f"{where}: {error}") from None
    target = table.get("target")
    if not isinstance(target, str) or not target:
        raise ValueError(f"{where} needs a target (the AMS net id) or a file")
    variables = table.get("variables")
    if not isinstance(variables, list | dict) or not variables:
        raise ValueError(
            f'variables in {where} must be names (["MAIN.*"]) or a table of types'
        )
    if isinstance(variables, dict):
        for name, given in variables.items():
            if isinstance(given, dict) and given.get("write") is True:
                raise ValueError(
                    f"{where} never writes to the PLC; drop write = true from {name}"
                )
    for key in ("types", "ip"):
        if key in table:
            if not isinstance(table[key], str):
                raise ValueError(f"{key} in {where} must be text")
            options[key] = table[key]
    return AdsSource(target, variables, **options)


def _decoder(table: dict[str, Any], where: str) -> Decoder:
    from .. import BinaryFrameDecoder, CsvDecoder, JsonDecoder, KeyValueDecoder

    kind = table.get("decoder", "keyvalue")
    if kind not in DECODERS:
        raise ValueError(
            f"decoder in {where} must be {' or '.join(DECODERS)}, not {kind!r}"
        )
    for key, wanted in (("fields", "csv"), ("names", "binary")):
        if key in table and kind != wanted:
            raise ValueError(f'{key} in {where} goes with decoder = "{wanted}"')
    if kind == "csv":
        fields = table.get("fields")
        if (
            not isinstance(fields, list)
            or not fields
            or not all(isinstance(f, str) for f in fields)
        ):
            raise ValueError(f"{where}: decoder csv needs fields, a list of ids")
        return CsvDecoder(fields)
    if kind == "binary":
        names = table.get("names")
        if (
            not isinstance(names, dict)
            or not names
            or not all(k.isdigit() and isinstance(v, str) for k, v in names.items())
        ):
            raise ValueError(
                f'{where}: decoder binary needs names, a table like {{ "1" = "X" }}'
            )
        return BinaryFrameDecoder({int(byte): name for byte, name in names.items()})
    return JsonDecoder() if kind == "json" else KeyValueDecoder()


def _zmq(table: dict[str, Any], where: str, base: Path) -> Source:
    from .. import ZmqSource

    pattern = table.get("pattern", "sub")
    if pattern not in ("sub", "pull"):
        raise ValueError(f"pattern in {where} must be sub or pull, not {pattern!r}")
    endpoint = table.get("endpoint", "tcp://localhost:5556")
    if not isinstance(endpoint, str):
        raise ValueError(f"endpoint in {where} must be text")
    bind = table.get("bind")
    if bind is not None and not isinstance(bind, bool):
        raise ValueError(f"bind in {where} must be true or false")
    return ZmqSource(
        endpoint, decoder=_decoder(table, where), pattern=pattern, bind=bind
    )


def _serial(table: dict[str, Any], where: str, base: Path) -> Source:
    from .. import SerialSource

    port = table.get("port", "auto")
    if not isinstance(port, str):
        raise ValueError(f"port in {where} must be text")
    baudrate = int(number(table, "baudrate", 115200, where))
    return SerialSource(port, baudrate=baudrate, decoder=_decoder(table, where))


def _influxdb(table: dict[str, Any], where: str, base: Path) -> Source:
    from .. import InfluxSource

    for key in ("url", "org", "token", "bucket", "query", "measurement", "prefix"):
        if not isinstance(table.get(key, ""), str):
            raise ValueError(f"{key} in {where} must be text")
    for key in ("url", "org", "token"):
        if not table.get(key):
            raise ValueError(f"{where} needs a {key}")
    if bool(table.get("bucket")) == bool(table.get("query")):
        raise ValueError(f"{where} needs a bucket or a query, and not both")
    for key in ("fields", "id_from"):
        value = table.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{key} in {where} must be a list of names")
    tags = table.get("tags", {})
    if not isinstance(tags, dict) or not all(isinstance(v, str) for v in tags.values()):
        raise ValueError(f"tags in {where} must be a table of text")
    options: dict[str, Any] = {
        key: table[key] for key in ("measurement", "prefix") if key in table
    }
    if table.get("id_from"):
        options["id_from"] = table["id_from"]
    return InfluxSource(
        table["url"],
        org=table["org"],
        token=table["token"],
        bucket=table.get("bucket", ""),
        query=table.get("query", ""),
        fields=table.get("fields", []),
        tags=tags,
        window=number(table, "window", 60.0, where),
        interval=number(table, "interval", 1.0, where),
        timeout=number(table, "timeout", 10.0, where),
        **options,
    )


SOURCES = {
    "rest": _rest,
    "ads": _ads,
    "zmq": _zmq,
    "serial": _serial,
    "influxdb": _influxdb,
}
