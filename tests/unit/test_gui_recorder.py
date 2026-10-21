"""Recorder tab saves bot routes and records (T8.F1.K1, T8.F3.K1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonlight.bot.state import Position
from moonlight.decision.cavebot import RouteError, RouteFileLoader, Waypoint
from moonlight.gui.tabs.recorder import make_waypoint, save_route, should_add, status_line


def test_saved_route_loads_with_the_bot_loader(tmp_path: Path) -> None:
    wps = [
        Waypoint(100, 200, 7, "walk"),
        Waypoint(0, 0, 0, "label", "start"),
        Waypoint(101, 200, 7, "rope"),
        Waypoint(0, 0, 0, "goto", "start"),
    ]
    path = save_route(tmp_path / "waypoints", "rotworms", wps)
    route = RouteFileLoader.load(path)
    assert route.name == "rotworms"
    assert route.waypoints == wps


def test_bad_routes_are_refused(tmp_path: Path) -> None:
    with pytest.raises(RouteError):
        save_route(tmp_path, "rotworms", [])
    with pytest.raises(RouteError):
        save_route(tmp_path, "../x", [Waypoint(1, 1, 7)])


def test_make_waypoint() -> None:
    live = Position(5, 6, 7)
    assert make_waypoint("shovel", "", ("", "", ""), live) == Waypoint(5, 6, 7, "shovel")
    assert make_waypoint("walk", "", ("1", "2", "8"), live) == Waypoint(1, 2, 8, "walk")
    assert make_waypoint("goto", " a ", ("", "", ""), live) == Waypoint(0, 0, 0, "goto", "a")
    with pytest.raises(RouteError, match="Position unknown"):
        make_waypoint("walk", "", ("", "", ""), Position(0, 0, 0))
    with pytest.raises(RouteError):
        make_waypoint("label", "", ("", "", ""), live)


def test_short_step_adds_nothing() -> None:
    wps = [Waypoint(100, 100, 7)]
    assert not should_add(wps, Position(104, 96, 7), 5)
    assert not should_add(wps, Position(0, 0, 0), 5)


def test_long_step_or_floor_change_adds() -> None:
    wps = [Waypoint(100, 100, 7), Waypoint(0, 0, 0, "label", "a")]
    assert should_add(wps, Position(105, 100, 7), 5)
    assert should_add(wps, Position(100, 100, 8), 5)
    assert should_add([], Position(1, 1, 7), 5)


def test_status_line() -> None:
    assert status_line(False, Position(0, 0, 0)) == "Not recording · position unknown"
    assert status_line(True, Position(1, 2, 7)) == "Recording · 1, 2, 7"
