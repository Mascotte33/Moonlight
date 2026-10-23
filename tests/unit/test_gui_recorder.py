"""Recorder tab saves bot routes and records (T8.F1.K1-T8.F4.K1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonlight.bot.state import Position
from moonlight.decision.cavebot import RouteError, RouteFileLoader, Waypoint
from moonlight.gui.tabs.recorder import (
    HOTKEYS,
    make_waypoint,
    position_line,
    route_path,
    save_route,
    should_add,
    vk_code,
)


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


def test_negative_coordinates_save(tmp_path: Path) -> None:
    wps = [Waypoint(-5, -12, 7, "stand")]
    assert RouteFileLoader.load(save_route(tmp_path, "minus", wps)).waypoints == wps


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


def test_position_line() -> None:
    assert position_line(Position(0, 0, 0)) == "Position unknown"
    assert position_line(Position(1, 2, 7)) == "Position 1, 2, 7"
    assert position_line(Position(1, 2, 7), "lost") == "Position 1, 2, 7 (lost)"


def test_each_hotkey_maps_to_its_action() -> None:
    assert HOTKEYS == {
        "F6": "walk",
        "F7": "rope",
        "F8": "shovel",
        "F4": "ladder",
        "F9": "label",
        "F5": "undo",
    }
    assert [vk_code(k) for k in ("F4", "F5", "F6", "F7", "F8", "F9")] == [
        0x73,
        0x74,
        0x75,
        0x76,
        0x77,
        0x78,
    ]


def test_route_path_refuses_bad_names(tmp_path: Path) -> None:
    assert route_path(tmp_path, "rot worms") == tmp_path / "rot worms.json"
    for bad in ("", "../x", "a/b"):
        with pytest.raises(RouteError):
            route_path(tmp_path, bad)
