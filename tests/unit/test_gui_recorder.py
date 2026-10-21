"""Recorder tab saves bot routes (T8.F1.K1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonlight.bot.state import Position
from moonlight.decision.cavebot import RouteError, RouteFileLoader, Waypoint
from moonlight.gui.tabs.recorder import make_waypoint, save_route


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
