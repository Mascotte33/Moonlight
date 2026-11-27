"""Floor changes, multi-floor paths, client markers and route warnings."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from moonlight.cavebot.pathing import CostMap
from moonlight.cavebot.route import Route, Waypoint
from moonlight.cavebot.route_plan import (
    CITY_DEPOTS,
    FloorChange,
    FloorMap,
    Link,
    find_path,
    nearest_city,
    picture_files,
    read_markers,
    route_links,
    route_warnings,
    spawn_points,
)

YELLOW = (0, 255, 255)


def _map(tmp_path: Path, yellow: dict[int, list[tuple[int, int]]]) -> CostMap:
    """Floors 7 and 8 of tile (0, 0): all walkable grey, *yellow* sqm per floor."""
    (tmp_path / "cost").mkdir()
    for z in (7, 8):
        colour = np.full((256, 256, 3), 120, np.uint8)
        for x, y in yellow.get(z, []):
            colour[y, x] = YELLOW
        cv2.imwrite(str(tmp_path / f"0_0_{z}.png"), colour)
        cv2.imwrite(str(tmp_path / "cost" / f"0_0_{z}.png"), np.full((256, 256, 3), 50, np.uint8))
    return CostMap(tmp_path / "cost")


def test_path_takes_the_stairs_down(tmp_path: Path) -> None:
    floors = FloorMap(_map(tmp_path, {7: [(10, 10)], 8: [(11, 10)]}), {})
    steps = find_path((5, 5, 7), (15, 15, 8), floors)
    assert steps is not None and steps[-1] == (15, 15, 8)
    changes = [s for s in steps if isinstance(s, FloorChange)]
    assert len(changes) == 1
    change = changes[0]
    assert change.at == (10, 10, 7) and change.kind == "stairs" and not change.known
    assert change.to[2] == 8 and max(abs(change.to[0] - 11), abs(change.to[1] - 10)) == 1


def test_known_rope_only_goes_up(tmp_path: Path) -> None:
    floors = FloorMap(_map(tmp_path, {7: [(10, 10)], 8: [(10, 10)]}), {(10, 10, 8): "rope"})
    (change,) = floors.changes_at(10, 10, 8)
    assert change.kind == "rope" and change.known and change.to[2] == 7


def test_no_path_without_a_floor_change(tmp_path: Path) -> None:
    assert find_path((5, 5, 7), (15, 15, 8), FloorMap(_map(tmp_path, {}), {}), margin=5) is None


def test_route_links_are_door_scripts_and_boat_talks() -> None:
    route = [
        Waypoint(10, 10, 7, "stand"),
        Waypoint(10, 10, 7, "script", "door_down"),
        Waypoint(70, 10, 9, "label", "Inside"),  # a label's sqm is not walked
        Waypoint(70, 11, 9),
        Waypoint(0, 0, 0, "say", "hi"),
        Waypoint(75, 11, 9),  # a talk then a short walk: not a trip
        Waypoint(0, 0, 0, "say", "hi"),
        Waypoint(0, 0, 0, "say", "venore"),
        Waypoint(0, 0, 0, "goto", "Inside"),  # a jump: not a trip
        Waypoint(200, 11, 9),
        Waypoint(0, 0, 0, "say", "yes"),
        Waypoint(200, 200, 9),
    ]
    assert route_links(route) == [
        Link((10, 10, 7), (70, 11, 9), ("script door_down",)),
        Link((200, 11, 9), (200, 200, 9), ("say yes",)),
    ]


def test_path_takes_a_link(tmp_path: Path) -> None:
    link = Link((5, 5, 7), (100, 100, 8), ("script door_down",))
    floors = FloorMap(_map(tmp_path, {}), {}, {link.at: [link]})
    assert find_path((5, 5, 7), (101, 100, 8), floors, margin=5) == [link, (101, 100, 8)]


def test_nearest_city_within_its_depot() -> None:
    x, y, z = CITY_DEPOTS["Venore"]
    assert nearest_city(x + 10, y - 10, z - 1) == "Venore"
    assert nearest_city(x + 10, y, z - 3) is None  # too many floors away
    assert nearest_city(x + 100, y, z) is None


def _varint(value: int) -> bytes:
    out = b""
    while value >= 0x80:
        out += bytes([value & 0x7F | 0x80])
        value >>= 7
    return out + bytes([value])


def test_read_markers(tmp_path: Path) -> None:
    where = b"\x08" + _varint(32369) + b"\x10" + _varint(32220) + b"\x18" + _varint(7)
    text = b"Depot"
    marker = b"\x0a" + _varint(len(where)) + where + b"\x10\x05\x1a" + _varint(len(text)) + text
    marker += b"\x20\x00"
    path = tmp_path / "minimapmarkers.bin"
    path.write_bytes(b"\x0a" + _varint(len(marker)) + marker)
    assert read_markers(path) == [((32369, 32220, 7), "Depot")]


def test_spawn_points(tmp_path: Path) -> None:
    def entry(name: str, spawns: list[dict[str, int]]) -> dict[str, object]:
        return {"monster": {"name": name}, "spots": [{"spawns": spawns}]}

    path = tmp_path / "spawns.json"
    sqm = {"x": 1, "y": 2, "z": 7}
    path.write_text(json.dumps([entry("Troll", [sqm, sqm]), entry("Rat", [sqm])]))
    assert spawn_points(path) == {(1, 2, 7): ["Troll", "Rat"]}


def test_picture_files_by_monster_name(tmp_path: Path) -> None:
    spot = {"centerX": 5, "centerY": 6, "floorBreakdown": {"7": 2, "8": 9}, "spawns": []}
    monster = {"name": "Troll", "image": "{{URL}}/monsters/Troll.gif"}
    path = tmp_path / "spawns.json"
    path.write_text(json.dumps([{"monster": monster, "spots": [spot]}]))
    assert picture_files(path, tmp_path) == {"Troll": tmp_path / "Troll.gif"}


def test_route_warnings(tmp_path: Path) -> None:
    route = Route(
        "hunt",
        waypoints=[Waypoint(5, 5, 7, "ladder"), Waypoint(6, 6, 7, "trade")],
    )
    warnings = route_warnings(route, _map(tmp_path, {}))
    assert any("waypoint 1: ladder" in w for w in warnings)
    assert any("no check" in w for w in warnings)
    assert any("empty buy list" in w for w in warnings)
