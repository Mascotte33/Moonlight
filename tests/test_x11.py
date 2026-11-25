"""Window lookup: any Tibia client, the focused one first."""

# doc: docs/prs/auto-calibration.md#game-window

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from moonlight import x11


@pytest.mark.parametrize(
    ("title", "is_client"),
    [
        ("Tibia", True),
        ("Tibia - Shabadeth", True),
        ("Tibia wiki - Firefox", False),
        ("Moonlight", False),
    ],
)
def test_only_the_game_client_counts(title: str, is_client: bool) -> None:
    assert x11.is_tibia_title(title) is is_client
    assert x11._matches(title, "Tibia") is is_client


def _fake_display(windows: dict[int, tuple[str, int]], active: int) -> Any:
    """Windows id -> (title, area); *active* is focused."""
    d = mock.Mock()
    d.create_resource_object.side_effect = lambda _kind, wid: mock.Mock(
        get_geometry=mock.Mock(return_value=mock.Mock(width=windows[wid][1], height=1))
    )
    return d, windows, active


def test_the_focused_client_wins_over_a_bigger_one(monkeypatch: pytest.MonkeyPatch) -> None:
    d, windows, active = _fake_display({1: ("Tibia - Big", 900), 2: ("Tibia - Front", 100)}, 2)
    monkeypatch.setattr(x11, "_d", lambda: d)
    monkeypatch.setattr(x11, "_clients", lambda _d: list(windows))
    monkeypatch.setattr(x11, "_viewable", lambda _d, _w: True)
    monkeypatch.setattr(x11, "_title", lambda _d, w: windows[w][0])
    monkeypatch.setattr(x11, "_active", lambda _d: active)
    monkeypatch.setattr(x11, "_cached", {})
    assert x11.find_window("Tibia") == 2


def test_without_a_focused_client_the_biggest_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    d, windows, _ = _fake_display({1: ("Tibia", 400), 2: ("Tibia - Game", 900)}, 0)
    monkeypatch.setattr(x11, "_d", lambda: d)
    monkeypatch.setattr(x11, "_clients", lambda _d: list(windows))
    monkeypatch.setattr(x11, "_viewable", lambda _d, _w: True)
    monkeypatch.setattr(x11, "_title", lambda _d, w: windows[w][0])
    monkeypatch.setattr(x11, "_active", lambda _d: 99)
    monkeypatch.setattr(x11, "_cached", {})
    assert x11.find_window("Tibia") == 2
