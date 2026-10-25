"""Linux paths: /proc readings, X11 focus match, F12 edge in the GUI poller."""

from __future__ import annotations

import sys

import pytest

from moonlight import x11
from moonlight.gui import app, sysinfo
from moonlight.perception.capture import ScreenCapture

STAT = "cpu  100 5 50 800 20 1 2 3 0 0\ncpu0 1 2 3 4 5 6 7 8 0 0\n"
MEMINFO = "MemTotal:        4000000 kB\nMemFree:          100000 kB\nMemAvailable:    1000000 kB\n"


def test_proc_stat_counts_iowait_as_idle() -> None:
    assert sysinfo.parse_proc_stat(STAT) == (820, 981)


def test_meminfo_values() -> None:
    assert sysinfo.parse_kb(MEMINFO, "MemAvailable") == 1000000
    assert sysinfo.parse_kb(MEMINFO, "VmRSS") is None


def test_focus_matches_the_title_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    cap = ScreenCapture(window_title="Tibia")
    monkeypatch.setattr(x11, "active_title", lambda: "Tibia - Moonlight")
    assert cap.is_focused()
    monkeypatch.setattr(x11, "active_title", lambda: "Terminal")
    assert not cap.is_focused()


def test_f12_fires_once_per_press_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(app, "_f12_was_down", False)
    held = iter([True, True, False, True])
    monkeypatch.setattr(x11, "vk_down", lambda vk: next(held))
    assert [app.f12_pressed() for _ in range(4)] == [True, False, False, True]


def test_only_function_keys_are_read() -> None:
    assert x11.vk_down(0x41) is False  # "A": never polled, no X needed
