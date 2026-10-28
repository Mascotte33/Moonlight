"""Recorder tab saves bot routes and records (T8.F1.K1-T8.F4.K1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonlight.cavebot.cavebot import RouteError, RouteFileLoader, Waypoint
from moonlight.cavebot.recorder import (
    StandTracker,
    make_waypoint,
    position_line,
    route_path,
    save_route,
    should_add,
    should_stop_recording,
    stairs_waypoint,
)
from moonlight.core.state import Position


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
    wps = [Waypoint(-5, -12, 7, "stand"), Waypoint(1, 2, 7, "step", "q")]
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


def test_route_path_refuses_bad_names(tmp_path: Path) -> None:
    assert route_path(tmp_path, "rot worms") == tmp_path / "rot worms.json"
    for bad in ("", "../x", "a/b"):
        with pytest.raises(RouteError):
            route_path(tmp_path, bad)


def test_recording_stops_when_the_cavebot_starts() -> None:
    assert should_stop_recording(True, True)
    assert not should_stop_recording(True, False)
    assert not should_stop_recording(False, True)  # nothing to stop


class _Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def _walk_east(tracker: StandTracker, clock: _Clock, start: int, tiles: int) -> list[Position]:
    """10 Hz reads; the minimap reports each tile at step start, a step takes 3 reads.

    Returns the tile the tracker confirmed after every read.
    """
    confirmed: list[Position] = []
    for x in range(start, start + tiles):
        for _ in range(3):
            tracker.update(Position(x, 50, 7))
            stood = tracker.stood
            if stood is not None:
                confirmed.append(stood)
            clock.now += 0.1
    return confirmed


def test_live_position_is_the_tile_being_walked_to() -> None:
    """Sampling the live read, a waypoint lands on a tile the character has not reached yet.

    A step to x=105 starts at x=104: the read already says 105 on the first
    tick, so ``should_add`` records 105 while the character still stands on 104.
    """
    standing_on = 104
    read = Position(105, 50, 7)  # the minimap re-centered on the destination
    assert should_add([Waypoint(100, 50, 7)], read, 5)
    assert read.x != standing_on


def test_stand_tracker_confirms_the_tile_left_while_walking() -> None:
    clock = _Clock()
    tracker = StandTracker(settle=0.6, clock=clock)
    confirmed = _walk_east(tracker, clock, 100, 8)
    # 3 reads per tile: the first tile is unconfirmed, then each read is one tile behind.
    assert confirmed == [Position(100 + i // 3 - 1, 50, 7) for i in range(3, 24)]


def test_stand_tracker_confirms_the_current_tile_after_it_settles() -> None:
    clock = _Clock()
    tracker = StandTracker(settle=0.6, clock=clock)
    tracker.update(Position(100, 50, 7))
    assert tracker.stood is None  # nothing known to have been stood on yet
    clock.now = 0.7
    assert tracker.stood == Position(100, 50, 7)
    tracker.update(Position(101, 50, 7))  # step starts
    assert tracker.stood == Position(100, 50, 7)
    clock.now = 1.4
    assert tracker.stood == Position(101, 50, 7)


def test_stand_tracker_ignores_unknown_reads() -> None:
    clock = _Clock()
    tracker = StandTracker(settle=0.6, clock=clock)
    tracker.update(Position(100, 50, 7))
    tracker.update(Position(0, 0, 0))
    clock.now = 1.0
    assert tracker.stood == Position(100, 50, 7)


def test_recorded_waypoints_are_tiles_the_character_stood_on() -> None:
    clock = _Clock()
    tracker = StandTracker(settle=0.6, clock=clock)
    waypoints = [Waypoint(100, 50, 7)]
    for x in range(100, 125):
        for _ in range(3):
            tracker.update(Position(x, 50, 7))
            stood = tracker.stood
            if stood is not None and should_add(waypoints, stood, 5):
                waypoints.append(Waypoint(stood.x, stood.y, stood.z))
            clock.now += 0.1
        # the character is on x only once it takes the next step
        assert all(wp.x < x for wp in waypoints[1:])
    assert [wp.x for wp in waypoints] == [100, 105, 110, 115, 120]


def test_a_floor_change_by_walking_records_the_stairs_tile() -> None:
    wps = [Waypoint(100, 105, 7)]
    ramp_up = stairs_waypoint(wps, Position(100, 101, 7), Position(100, 99, 6))
    assert ramp_up == Waypoint(100, 100, 7, "stairs")
    hole = stairs_waypoint(wps, Position(99, 100, 7), Position(100, 100, 8))
    assert hole == Waypoint(100, 100, 7, "stairs")
    assert stairs_waypoint(wps, Position(100, 101, 7), Position(100, 100, 7)) is None
    roped = [*wps, Waypoint(100, 101, 7, "rope")]
    assert stairs_waypoint(roped, Position(100, 101, 7), Position(100, 100, 6)) is None
