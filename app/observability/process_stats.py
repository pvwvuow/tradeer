"""This process's CPU and memory use (spec E3) without extra packages.

CPU time comes from `time.process_time()` (on Windows that is `GetProcessTimes`). Memory is
the working set from `GetProcessMemoryInfo` (psapi, through ctypes) on Windows and the
resident set from `/proc/self/statm` on Linux; None where neither works.
"""

from __future__ import annotations

import ctypes
import os
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

CPU_SAMPLES = 5  # the CPU figure is the average of the last five readings (minutes)
STATM = Path("/proc/self/statm")
DEFAULT_PAGE_BYTES = 4096


class _MemoryCounters(ctypes.Structure):
    """PROCESS_MEMORY_COUNTERS of the Windows API."""

    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def windows_memory() -> float | None:
    """The working set in bytes, or None when the Windows API is not there."""
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        return None
    try:
        # Own library objects: changing restype/argtypes must not touch ctypes.windll's.
        kernel32 = loader("kernel32")
        psapi = loader("psapi")
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        info = psapi.GetProcessMemoryInfo
        info.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        info.restype = ctypes.c_int
        counters = _MemoryCounters()
        counters.cb = ctypes.sizeof(_MemoryCounters)
        if not info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return float(counters.WorkingSetSize)
    except (AttributeError, OSError, ValueError):
        return None


def _page_bytes() -> int:
    sysconf = getattr(os, "sysconf", None)
    if sysconf is None:
        return DEFAULT_PAGE_BYTES
    try:
        return int(sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError):
        return DEFAULT_PAGE_BYTES


def statm_memory(path: Path = STATM) -> float | None:
    """The resident set in bytes from Linux's `/proc/self/statm` (second field, in pages)."""
    try:
        fields = path.read_text(encoding="ascii").split()
        pages = int(fields[1])
    except (OSError, ValueError, IndexError):
        return None
    return float(pages * _page_bytes())


def memory_bytes() -> float | None:
    if os.name == "nt":
        return windows_memory()
    return statm_memory()


def cpu_seconds() -> float:
    return time.process_time()


class CpuMeter:
    """This process's CPU use in percent of the whole PC, averaged over the last readings.

    The first `sample()` only starts the clock (None); every later one adds the share of the
    time since the previous sample.
    """

    def __init__(
        self,
        cpu: Callable[[], float] = cpu_seconds,
        wall: Callable[[], float] = time.perf_counter,
        cores: int | None = None,
        samples: int = CPU_SAMPLES,
    ) -> None:
        self._cpu = cpu
        self._wall = wall
        self._cores = max(cores or os.cpu_count() or 1, 1)
        self._last: tuple[float, float] | None = None
        self._readings: deque[float] = deque(maxlen=max(samples, 1))

    def sample(self) -> float | None:
        cpu, wall = self._cpu(), self._wall()
        last, self._last = self._last, (cpu, wall)
        if last is not None and wall > last[1]:
            used = max(cpu - last[0], 0.0)
            self._readings.append(used / (wall - last[1]) / self._cores * 100.0)
        return self.average

    @property
    def average(self) -> float | None:
        if not self._readings:
            return None
        return sum(self._readings) / len(self._readings)
