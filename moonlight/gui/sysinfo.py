"""Whole-PC and own-process readings for the bottom bar and Status (T9.F1.K1).

Windows through ``ctypes``, Linux through ``/proc``; every reader gives
``None`` or ``"—"`` elsewhere or on failure, so a status label never crashes
the poller.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

__all__ = [
    "cpu_percent",
    "cpu_times",
    "memory_mb",
    "memory_text",
    "parse_kb",
    "parse_proc_stat",
    "ram_percent",
]


def _proc(name: str) -> str | None:
    try:
        return Path("/proc", name).read_text()
    except OSError:
        return None


def parse_proc_stat(text: str) -> tuple[int, int]:
    """``(idle, total)`` from the ``cpu`` line of ``/proc/stat``; idle counts iowait."""
    ticks = [int(v) for v in text.splitlines()[0].split()[1:9]]
    return ticks[3] + ticks[4], sum(ticks)


def parse_kb(text: str, key: str) -> int | None:
    """The ``key:  1234 kB`` value from ``/proc/meminfo`` or ``/proc/self/status``."""
    for line in text.splitlines():
        if line.startswith(key + ":"):
            return int(line.split()[1])
    return None


def cpu_times() -> tuple[int, int] | None:
    """Whole-PC ``(idle, total)`` CPU time in ticks (100 ns on Windows, jiffies on Linux)."""
    if sys.platform == "linux":
        text = _proc("stat")
        return None if text is None else parse_proc_stat(text)
    if sys.platform != "win32":
        return None
    idle, kernel, user = (wintypes.FILETIME() for _ in range(3))
    ok = ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    )
    if not ok:
        return None
    ticks = [(t.dwHighDateTime << 32) | t.dwLowDateTime for t in (idle, kernel, user)]
    return ticks[0], ticks[1] + ticks[2]  # kernel time includes idle


def cpu_percent(prev: tuple[int, int], now: tuple[int, int]) -> float:
    """Busy share of the whole PC between two :func:`cpu_times` samples, 0..100."""
    total = now[1] - prev[1]
    if total <= 0:
        return 0.0
    return 100.0 * (1 - (now[0] - prev[0]) / total)


def memory_mb() -> int | None:
    """Working set (resident memory) of this process in MB; None when unknown."""
    if sys.platform == "linux":
        text = _proc("self/status")
        kb = None if text is None else parse_kb(text, "VmRSS")
        return None if kb is None else kb // 1024
    if sys.platform != "win32":
        return None
    try:

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

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        c = Counters()
        c.cb = ctypes.sizeof(c)
        if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return None
        return int(c.WorkingSetSize // 2**20)
    except Exception:  # noqa: BLE001 - a status label must never crash the poller
        return None


def memory_text() -> str:
    """Working set of this process as "85 MB"; "—" when unknown."""
    mb = memory_mb()
    return "—" if mb is None else f"{mb} MB"


def ram_percent() -> float | None:
    """Share of the whole PC's RAM in use, 0..100; None when unknown."""
    if sys.platform == "linux":
        text = _proc("meminfo")
        total = None if text is None else parse_kb(text, "MemTotal")
        free = None if text is None else parse_kb(text, "MemAvailable")
        if not total or free is None:
            return None
        return 100.0 * (1 - free / total)
    if sys.platform != "win32":
        return None

    class Status(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            *(
                (n, ctypes.c_ulonglong)
                for n in (
                    "ullTotalPhys",
                    "ullAvailPhys",
                    "ullTotalPageFile",
                    "ullAvailPageFile",
                    "ullTotalVirtual",
                    "ullAvailVirtual",
                    "ullAvailExtendedVirtual",
                )
            ),
        ]

    st = Status()
    st.dwLength = ctypes.sizeof(st)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return None
    return float(st.dwMemoryLoad)
