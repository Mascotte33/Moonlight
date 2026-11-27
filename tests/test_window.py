"""The window seam: one adapter per OS, chosen once."""

from __future__ import annotations

import pytest

from moonlight import window
from moonlight.window import NoWindows, Win32Windows, X11Windows, for_platform


def test_each_os_gets_its_adapter() -> None:
    assert isinstance(for_platform("linux"), X11Windows)
    assert isinstance(for_platform("win32"), Win32Windows)
    assert isinstance(for_platform("darwin"), NoWindows)
    assert for_platform("win32").restarts_client and not for_platform("linux").restarts_client


def test_linux_reads_in_game_from_the_title(monkeypatch: pytest.MonkeyPatch) -> None:
    titles = {7: "Tibia - Shabadeth"}
    monkeypatch.setattr(window.x11, "find_window", lambda _t: 7)
    monkeypatch.setattr(window.x11, "title_of", lambda wid: titles.get(wid))
    assert X11Windows().in_game("Tibia") is True
    titles[7] = "Tibia"  # the login screen
    assert X11Windows().in_game("Tibia") is False
    assert Win32Windows().in_game("Tibia") is None  # Windows looks at the screen


def test_linux_focus_matches_the_active_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(window.x11, "active_title", lambda: "Tibia - Shabadeth")
    assert X11Windows().focused("Tibia", lambda: None)
    monkeypatch.setattr(window.x11, "active_title", lambda: "Firefox")
    assert not X11Windows().focused("Tibia", lambda: None)


def test_focus_window_is_false_without_a_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(window, "WINDOWS", NoWindows())
    assert window.focus_window("Tibia") is False
