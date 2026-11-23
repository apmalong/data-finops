"""Phase timings (ADR 0033, Q184): one JSON log line for each phase of a function's run, with
the seconds that the phase took and the peak memory of the process so far.

    {"event": "phase", "function": "publisher", "phase": "render", "seconds": 1.234,
     "peak_mib": 265}

The lines go to stderr, apart from what a function prints as its result. On Lambda, both go to
CloudWatch Logs. `peak_mib` is the largest resident memory of the process so far, in MiB (the
unit that Lambda calls "MB"), so it only grows from one phase to the next. It is null where the
platform does not report it.

Stdlib only.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass


def peak_memory_mib() -> int | None:
    """The peak resident memory of this process so far, in MiB, or None if it is unknown."""
    if sys.platform == "win32":
        return _windows_peak()
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1 << 20 if sys.platform == "darwin" else 1 << 10)) or None


def _windows_peak() -> int | None:  # local runs only: Lambda is Linux
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
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

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined,unused-ignore]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE  # pointer-sized, not the default int
    kernel.K32GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    process = kernel.GetCurrentProcess()
    if not kernel.K32GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
        return None
    return round(counters.PeakWorkingSetSize / (1 << 20)) or None


def _to_stderr(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


@dataclass(frozen=True)
class Phases:
    """Times the phases of one function. `with phases("render"): ...` logs one line when the
    phase ends. A phase that raises logs nothing: the error is in the log."""

    function: str
    log: Callable[[str], None] = _to_stderr

    @contextmanager
    def __call__(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        yield
        line = {
            "event": "phase",
            "function": self.function,
            "phase": name,
            "seconds": round(time.perf_counter() - start, 3),
            "peak_mib": peak_memory_mib(),
        }
        self.log(json.dumps(line))


NO_PHASES = Phases("", log=lambda line: None)
"""Phases that log nothing, for a caller outside a Deployment's functions."""
