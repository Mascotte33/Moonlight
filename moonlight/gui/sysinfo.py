"""Whole-PC and own-process readings for the bottom bar and Status (T9.F1.K1).

Windows only, through ``ctypes``; every reader gives ``None`` or ``"—"``
elsewhere or on failure, so a status label never crashes the poller.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

__all__ = ["cpu_percent", "cpu_times", "memory_mb", "memory_text", "ram_percent"]


def cpu_times() -> tuple[int, int] | None:
    """Whole-PC ``(idle, total)`` CPU time in 100 ns ticks; None off Windows."""
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
    """Working set of this process in MB; None off Windows or on failure."""
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
    """Share of the whole PC's RAM in use, 0..100; None off Windows."""
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
