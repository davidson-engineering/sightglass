"""A program's log records on its dashboard: :class:`LogHandler`."""

from __future__ import annotations

import logging
import threading
from typing import Any, Protocol

from .elements import LogMonitor
from .monitor import Monitor

__all__ = ["LogHandler"]


class _Target(Protocol):
    def set(self, id: str, value: Any) -> None: ...


class LogHandler(logging.Handler):
    """Send log records to a dashboard, one line each::

        logging.getLogger().addHandler(LogHandler(monitor))  # or a Client

    Lines read ``LEVEL message`` (another ``logging.Formatter`` can be set),
    so the page and the terminal show warnings and errors in their colours.
    With a :class:`~sightglass.Monitor`, a :class:`~sightglass.LogMonitor` of
    the last 20 lines, timestamped, is declared for ``id`` unless one is
    already. Like every handler, it never raises: a record it can't format or
    send goes to ``handleError``.

    A :class:`~sightglass.Client` can't report its own trouble through itself
    (it would send a line about each failed send, forever), so records from
    the ``sightglass.client`` logger are left out, as are records logged while
    this handler is sending one.
    """

    def __init__(self, target: _Target, id: str = "log", level: int = logging.NOTSET):
        super().__init__(level)
        self.target = target
        self.id = id
        self.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        self._sending = threading.local()
        if isinstance(target, Monitor) and id not in target:
            target.add(LogMonitor(id, lines=20, timestamp=True))

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == "sightglass.client" or getattr(self._sending, "on", False):
            return
        self._sending.on = True
        try:
            self.target.set(self.id, self.format(record))
        except Exception:  # a handler never raises into the code that logged
            self.handleError(record)
        finally:
            self._sending.on = False
