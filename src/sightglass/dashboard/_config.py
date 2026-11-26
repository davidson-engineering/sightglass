"""Reading a config file's tables: what every part of it shares."""

from __future__ import annotations

import os
import re
from typing import Any

ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def tables(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """The ``[[key]]`` tables of ``data``."""
    found = data.get(key, [])
    if not isinstance(found, list) or not all(isinstance(t, dict) for t in found):
        raise ValueError(f"write each {key} as a [[{key}]] table")
    return found


def only(table: dict[str, Any], allowed: set[str], where: str) -> None:
    """Refuse a key ``allowed`` doesn't have, naming those it does."""
    if unknown := sorted(set(table) - allowed):
        raise ValueError(
            f"{where} has unknown {', '.join(map(repr, unknown))}; "
            f"it takes {', '.join(sorted(allowed))}"
        )


def number(table: dict[str, Any], key: str, default: float, where: str) -> float:
    """``table[key]``, a number above 0."""
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"{key} in {where} must be a number above 0")
    return value


def expand(value: Any, where: str) -> Any:
    """``value`` with ``${NAME}`` / ``${NAME:-default}`` filled in, throughout."""
    if isinstance(value, str):

        def fill(match: re.Match[str]) -> str:
            name, default = match.groups()
            if (found := os.environ.get(name)) is not None:
                return found
            if default is not None:
                return default
            raise ValueError(f"{where}: the environment variable {name} is not set")

        return ENV.sub(fill, value)
    if isinstance(value, dict):
        return {key: expand(item, where) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item, where) for item in value]
    return value
