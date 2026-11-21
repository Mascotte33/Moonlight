"""Map view maths, leg checks and tile stitching (no window)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from moonlight.cavebot.cavebot import Waypoint
from moonlight.cavebot.map_tab import UndoStack, matching_spawns, tooltip
from moonlight.cavebot.route_map import (
    MapTiles,
    MapView,
    bad_legs,
    draw_route,
    leg_path,
    legs,
    waypoint_at,
)


def _route() -> list[Waypoint]:
    return [
        Waypoint(100, 100, 7),
        Waypoint(0, 0, 0, "label", "start"),
        Waypoint(105, 100, 7),
        Waypoint(110, 100, 7, "rope"),
        Waypoint(110, 100, 6),
    ]


def test_legs_skip_tileless_and_loop_back() -> None:
    assert legs(_route()) == [(0, 2), (2, 3), (3, 4), (4, 0)]
    assert legs(_route()[:3]) == [(0, 2)]  # two spots: no loop pair


def test_bad_legs_only_same_floor_without_path() -> None:
    wall_x = 108

    def walkable(x: int, y: int, z: int) -> bool:
        return x != wall_x

    path = leg_path(walkable)
    bad = bad_legs(_route(), path)
    assert bad == [(2, 3)]  # 105 -> 110 crosses the wall; floor changes are not checked
    assert path((100, 100, 7), (103, 100, 7)) == [(101, 100), (102, 100), (103, 100)]


def test_waypoint_at_finds_last_positional() -> None:
    route = [*_route(), Waypoint(100, 100, 7, "stand")]
    assert waypoint_at(route, 100, 100, 7) == 5
    assert waypoint_at(route, 0, 0, 0) is None  # the label has no sqm
    assert waypoint_at(route, 100, 100, 6) is None


def test_view_sqm_pan_zoom() -> None:
    view = MapView(1000, 2000, 7, scale=4, width=400, height=200)
    assert view.origin == (950, 1975)
    assert view.sqm_at(200, 100) == (1000, 2000)
    view.pan(40, -8)  # drag right 10 sqm, up 2 sqm
    assert (view.x, view.y) == (990, 2002)
    before = view.sqm_at(30, 170)
    view.zoom(2, 30, 170)
    assert view.scale == 8
    assert view.sqm_at(30, 170) == before
    view.zoom(99, 0, 0)
    assert view.scale == 16


def test_tiles_stitch_across_tile_edges(tmp_path: Path) -> None:
    left = np.full((256, 256, 3), (0, 0, 255), np.uint8)  # BGR red
    cv2.imwrite(str(tmp_path / "0_0_7.png"), left)
    area = MapTiles(tmp_path).area(250, 10, 7, 12, 3)
    assert area.shape == (3, 12, 3)
    assert tuple(area[0, 0]) == (255, 0, 0)  # RGB red
    assert not area[:, 6:].any()  # tile 256_0_7 missing: black


def test_render_size_and_tooltip() -> None:
    view = MapView(105, 100, 7, scale=6, width=301, height=199)
    image = view.render(MapTiles(Path("nowhere")), _route(), [(2, 3)], selected=0, me=(104, 100, 7))
    assert image.shape == (199, 301, 3)
    assert image.any()  # the route was drawn on the black map
    assert draw_route(np.zeros((5, 5, 3), np.uint8), (0, 0), 7, 2, [], []).shape == (10, 10, 3)
    assert tooltip(3, _route()[3]) == "4  rope  (110, 100, 7)"
    monsters = {(104, 99, 7): ["Troll"], (104, 99, 8): ["Rat"]}
    with_monsters = view.render(MapTiles(Path("nowhere")), [], [], monsters=monsters)
    assert with_monsters.any()  # the floor-7 spawn was drawn


def test_undo_restores_the_route_before_a_map_edit() -> None:
    route = _route()
    undo = UndoStack()
    before = list(route)
    del route[0]
    undo.push(before, route)
    assert undo.undo(route) and route == before
    assert not undo.undo(route)  # nothing left
    undo.push(list(route), route[:1])  # the after list no longer matches: an outside edit
    assert not undo.undo(route) and route == before


def test_matching_spawns_by_part_of_the_name() -> None:
    spawns = {(1, 1, 7): ["Troll", "Swamp Troll"], (2, 2, 7): ["Rat"]}
    assert matching_spawns(spawns, " troll") == {(1, 1, 7): ["Troll", "Swamp Troll"]}
    assert matching_spawns(spawns, "") is spawns
