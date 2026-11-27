"""A source that measures the program it runs in: CPU, memory, threads.

    monitor = start(sources=[ProcessSource()])

Every ``interval`` seconds it sends ``process.cpu`` (percent of one core
since the last sample), ``process.rss`` (resident memory, in bytes, shown in
MB), ``process.threads`` (Python threads), ``process.fds`` (open files and
sockets), ``process.uptime`` (seconds since the process started) and
``process.gc_collections`` / ``process.gc_collected`` (garbage collector
runs, and the objects they freed). It declares how they look, unless the
program declared those ids first.

Standard library only. psutil, when installed, is used for memory, open files
and uptime; without it they come from the operating system directly, except
on Windows, which has no count of open files, and uptime outside Linux, which
then counts from when sightglass was imported.
"""

from __future__ import annotations

import asyncio
import gc
import os
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from ..elements import Element, Sparkline, TextElement
from ..formatting import TextFormat
from ..monitor import Update

IMPORTED = time.time()  # uptime's start where the process's own isn't known


class Sampler:
    """Takes one sample of this process at a time, as ``{name: number}``.

    CPU is measured between samples, so the first sample has none (psutil's
    first ``cpu_percent()`` answers 0 instead)."""

    def __init__(self) -> None:
        try:
            import psutil
        except ImportError:
            psutil = None
        self._process = psutil.Process() if psutil else None
        self._rss = self._psutil_rss if psutil else _stdlib_rss()
        self._fds = self._psutil_fds if psutil else _stdlib_fds()
        self.started = self._process.create_time() if psutil else _started()
        self._cpu: tuple[float, float] | None = None  # CPU and wall clock

    @property
    def counts_files(self) -> bool:
        return self._fds is not None

    def sample(self) -> dict[str, float]:
        values: dict[str, float] = {}
        wall, cpu = time.perf_counter(), time.process_time()
        if self._cpu is not None and wall > self._cpu[1]:
            values["cpu"] = (cpu - self._cpu[0]) / (wall - self._cpu[1]) * 100
        self._cpu = cpu, wall
        if self._rss is not None and (rss := self._rss()) is not None:
            values["rss"] = rss
        values["threads"] = threading.active_count()
        if self._fds is not None:
            values["fds"] = self._fds()
        values["uptime"] = max(time.time() - self.started, 0.0)
        stats = gc.get_stats()
        values["gc_collections"] = sum(each["collections"] for each in stats)
        values["gc_collected"] = sum(each["collected"] for each in stats)
        return values

    def _psutil_rss(self) -> int:
        return self._process.memory_info().rss

    def _psutil_fds(self) -> int:
        if sys.platform == "win32":
            return self._process.num_handles()
        return self._process.num_fds()


class ProcessSource:
    """Samples the running program every ``interval`` seconds, as ids under
    ``prefix`` (``"process.cpu"``, ``"process.rss"``, ...)."""

    def __init__(self, *, interval: float = 1.0, prefix: str = "process") -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.interval = interval
        self.prefix = prefix
        self._sampler = Sampler()
        self.elements = self._elements()

    def _elements(self) -> list[Element]:
        p, whole = self.prefix, TextFormat(precision=0)
        tenths = TextFormat(precision=1)
        elements: list[Element] = [
            Sparkline(f"{p}.cpu", label="CPU", units="%", format=tenths),
            Sparkline(
                f"{p}.rss", label="Memory", units="MB", scale=1 / 2**20, format=tenths
            ),
            TextElement(f"{p}.threads", label="Threads", format=whole),
        ]
        if self._sampler.counts_files:
            elements.append(TextElement(f"{p}.fds", label="Open files", format=whole))
        elements += [
            TextElement(f"{p}.uptime", label="Uptime", units="s", format=whole),
            TextElement(f"{p}.gc_collections", label="GC runs", format=whole),
            TextElement(f"{p}.gc_collected", label="Freed by GC", format=whole),
        ]
        return elements

    async def updates(self) -> AsyncIterator[list[Update]]:
        # Each sample reads memory the operating system keeps (procfs, a
        # system call or two): no I/O that could hold up the event loop.
        while True:
            sample = self._sampler.sample()
            yield [{f"{self.prefix}.{name}": value for name, value in sample.items()}]
            await asyncio.sleep(self.interval)

    def __repr__(self) -> str:
        return f"ProcessSource(interval={self.interval}, prefix={self.prefix!r})"


# -- the standard library route, per platform ---------------------------------


def _stdlib_rss() -> Callable[[], int | None] | None:
    """A function returning this process's resident memory in bytes."""
    if sys.platform.startswith("linux"):
        page = os.sysconf("SC_PAGE_SIZE")

        def linux() -> int:
            with open("/proc/self/statm") as statm:
                return int(statm.read().split()[1]) * page

        return linux
    if sys.platform == "darwin":
        return _darwin_rss()
    if sys.platform == "win32":
        return _windows_rss()
    return None


def _darwin_rss() -> Callable[[], int | None]:
    import ctypes

    class TaskBasicInfo(ctypes.Structure):  # mach_task_basic_info
        _fields_ = [
            ("virtual_size", ctypes.c_uint64),
            ("resident_size", ctypes.c_uint64),
            ("resident_size_max", ctypes.c_uint64),
            ("user_time", ctypes.c_int32 * 2),
            ("system_time", ctypes.c_int32 * 2),
            ("policy", ctypes.c_int32),
            ("suspend_count", ctypes.c_int32),
        ]

    libc = ctypes.CDLL(None)
    task_info = libc.task_info
    task_info.argtypes = [
        ctypes.c_uint32,
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    task_info.restype = ctypes.c_int
    task = ctypes.c_uint32.in_dll(libc, "mach_task_self_").value
    mach_task_basic_info = 20

    def darwin() -> int | None:
        info = TaskBasicInfo()
        count = ctypes.c_uint32(ctypes.sizeof(info) // 4)  # in natural_t
        result = task_info(
            task, mach_task_basic_info, ctypes.byref(info), ctypes.byref(count)
        )
        return info.resident_size if result == 0 else None

    return darwin


def _windows_rss() -> Callable[[], int | None]:
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32: Any = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    memory_info = kernel32.K32GetProcessMemoryInfo
    memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    memory_info.restype = wintypes.BOOL

    def windows() -> int | None:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        process = kernel32.GetCurrentProcess()
        if not memory_info(process, ctypes.byref(counters), counters.cb):
            return None
        return counters.WorkingSetSize

    return windows


def _stdlib_fds() -> Callable[[], int] | None:
    """A function counting this process's open files, where one can."""
    for folder in ("/proc/self/fd", "/dev/fd"):  # Linux, then macOS and BSDs
        if os.path.isdir(folder):
            return lambda: len(os.listdir(folder)) - 1  # less listdir's own
    return None


def _started() -> float:
    """When this process started (a Unix time), or when sightglass was imported."""
    try:
        with open("/proc/self/stat") as stat:
            fields = stat.read().rpartition(")")[2].split()
        with open("/proc/stat") as system:
            boot = next(
                float(line.split()[1]) for line in system if line.startswith("btime")
            )
        return boot + int(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, StopIteration, IndexError, ValueError):
        return IMPORTED
