"""Plan routes on paper: floor changes and paths across floors, for ``tools/``.

A floor change is a yellow sqm on the colour map (stairs, ladder, hole, rope
spot). Where it leads is not on the map, so it is paired with a yellow sqm
within ``PAIR_SQM`` on the floor above or below; you arrive on the walkable
sqm next to that one. The kind the bot needs (walk onto it = ``stairs``,
use it = ``ladder``, ``rope``) is taken from the routes that already use that
sqm, else guessed: down = ``stairs``, up = ``ladder``.

# doc: docs/prs/route-tools.md#floor-changes
# doc: docs/prs/route-tools.md#path
"""

from __future__ import annotations

import heapq
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from moonlight.cavebot.cavebot import Route, RouteError, RouteFileLoader, has_tile
from moonlight.cavebot.pathing import WALK_STEPS, CostMap

__all__ = [
    "FLOOR_KINDS", "PICTURES_DIR", "SPAWNS_FILE", "SPAWNS_URL", "FloorChange", "FloorMap",
    "MonsterSpot", "download_pictures", "download_spawns", "find_path", "known_floor_kinds",
    "picture_spots", "spawn_points",
    "load_floor_map", "loop_sqm", "monster_spots", "parse_spot", "read_markers", "route_places",
    "route_warnings",
]  # fmt: skip

Spot = tuple[int, int, int]
#: Waypoint kinds that change the floor.
FLOOR_KINDS = ("stairs", "ladder", "rope", "shovel")
#: How far (sqm) the matching yellow sqm on the next floor may be.
PAIR_SQM = 2
#: Extra path cost of taking a floor change (a rope or ladder takes a moment).
CHANGE_COST = 2
#: The floor a used (not walked) floor change leads to; stairs go both ways.
_FLOOR_DELTA = {"rope": -1, "ladder": -1, "shovel": 1}


@dataclass(frozen=True)
class FloorChange:
    """Stepping on or using sqm *at* puts you on *to* (another floor)."""

    at: Spot
    to: Spot
    kind: str
    known: bool  # True: a route already uses *at* with *kind*


def _ring(x: int, y: int, radius: int) -> list[tuple[int, int, int]]:
    """(distance, x, y) of every sqm within *radius* of (x, y), nearest first."""
    span = range(-radius, radius + 1)
    return sorted((max(abs(dx), abs(dy)), x + dx, y + dy) for dx in span for dy in span)


#: Waypoint kinds done at a town spot; the sqm is the last walked one before them.
TOWN_ACTIONS = ("finddepo", "deposit", "bank", "trade")


def _varint(data: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[i]
        value |= (byte & 0x7F) << shift
        i += 1
        if byte < 0x80:
            return value, i
        shift += 7


def _fields(data: bytes) -> list[tuple[int, int | bytes]]:
    """The (field number, value) pairs of a protobuf message (varint or length-delimited)."""
    out: list[tuple[int, int | bytes]] = []
    i = 0
    while i < len(data):
        key, i = _varint(data, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(data, i)
            out.append((field, value))
        elif wire == 2:
            size, i = _varint(data, i)
            out.append((field, data[i : i + size]))
            i += size
        else:
            raise ValueError(f"marker file: wire type {wire} at byte {i}")
    return out


# doc: docs/prs/route-tools.md#find
def read_markers(path: Path) -> list[tuple[Spot, str]]:
    """The client's map markers: ((x, y, z), text), from ``minimapmarkers.bin``.

    The file is protobuf: field 1 per marker = {1: {1: x, 2: y, 3: z}, 2: icon,
    3: text}. Read from the file itself; no spec was found.
    """
    out = []
    for field, marker in _fields(path.read_bytes()):
        if field != 1 or not isinstance(marker, bytes):
            continue
        parts = dict(_fields(marker))
        where, text = parts.get(1), parts.get(3, b"")
        if not isinstance(where, bytes) or not isinstance(text, bytes):
            raise ValueError(f"{path}: a marker with no position")
        xyz = dict(_fields(where))
        out.append(((int(xyz[1]), int(xyz[2]), int(xyz[3])), text.decode("utf-8", "replace")))
    return out


def route_places(routes_dir: Path) -> list[tuple[Spot, str]]:
    """Depot, bank and shop spots the saved routes use: ((x, y, z), "bank (MyHunt)")."""
    out = []
    for path in sorted(routes_dir.glob("*.json")):
        try:
            route = RouteFileLoader.load(path)
        except (RouteError, OSError, ValueError):
            continue
        at: Spot | None = None
        for wp in route.waypoints:
            if has_tile(wp):
                at = (wp.x, wp.y, wp.z)
            if wp.action in TOWN_ACTIONS and at is not None:
                out.append((at, f"{f'{wp.action} {wp.label}'.strip()} ({path.stem})"))
    return out


#: Every monster's hunting spots with each spawn's sqm (tibiaroute.com's task map).
SPAWNS_URL = "https://tibiaroute.com/api/delivery-task-spots"
#: Where ``tools/map.py refresh`` saves it (not in git).
SPAWNS_FILE = Path("data/spawns.json")
#: The monster pictures: ``image`` in the spawns file is ``{{URL}}/monsters/Name.gif``.
PICTURES_URL = "https://cdn.tibiaroute.com"
#: Where ``tools/map.py refresh`` saves them (not in git).
PICTURES_DIR = Path("data/monsters")


@dataclass(frozen=True)
class MonsterSpot:
    place: str
    centre: Spot  # z: the floor with the most spawns
    spawns: int
    premium: bool


def download_spawns(path: Path) -> int:
    """Save SPAWNS_URL to *path*; returns how many monsters it lists."""
    request = urllib.request.Request(SPAWNS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    monsters = json.loads(data)  # raises on a page that is not the list
    path.write_bytes(data)
    return len(monsters)


# doc: docs/prs/route-tools.md#monsters
def monster_spots(spawns_path: Path, name: str) -> list[MonsterSpot]:
    """Where monster *name* (any case) spawns, most spawns first."""
    entries = json.loads(spawns_path.read_text(encoding="utf-8"))
    out = []
    for entry in entries:
        if entry["monster"]["name"].lower() != name.lower():
            continue
        for spot in entry["spots"]:
            out.append(
                MonsterSpot(
                    spot["locationName"], _centre(spot), spot["totalSpawns"], spot["premiumAccount"]
                )
            )
    return sorted(out, key=lambda s: -s.spawns)


def _centre(spot: dict[str, Any]) -> Spot:
    """A hunting spot's centre, on the floor with the most spawns."""
    floors: dict[str, int] = spot["floorBreakdown"]
    return spot["centerX"], spot["centerY"], int(max(floors, key=lambda floor: floors[floor]))


def _picture_file(entry: dict[str, Any], directory: Path) -> Path:
    return directory / str(entry["monster"]["image"]).rsplit("/", 1)[-1]


# doc: docs/prs/route-tools.md#monster-pictures
def picture_spots(spawns_path: Path, directory: Path) -> list[tuple[Spot, str, Path]]:
    """(centre, monster name, picture file) of every hunting spot."""
    return [
        (_centre(spot), entry["monster"]["name"], _picture_file(entry, directory))
        for entry in json.loads(spawns_path.read_text(encoding="utf-8"))
        for spot in entry["spots"]
    ]


def download_pictures(spawns_path: Path, directory: Path) -> int:
    """Save each monster's picture into *directory* (skips saved ones); returns how many."""
    directory.mkdir(parents=True, exist_ok=True)
    todo = []
    for entry in json.loads(spawns_path.read_text(encoding="utf-8")):
        file = _picture_file(entry, directory)
        if not file.exists():
            todo.append((entry["monster"]["image"].replace("{{URL}}", PICTURES_URL), file))

    def save(url_and_file: tuple[str, Path]) -> None:
        url, file = url_and_file
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            file.write_bytes(response.read())

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(save, todo))  # list() re-raises a failed download
    return len(todo)


def spawn_points(spawns_path: Path) -> dict[Spot, list[str]]:
    """Every spawn sqm in *spawns_path* and the monsters that spawn there."""
    points: dict[Spot, list[str]] = {}
    for entry in json.loads(spawns_path.read_text(encoding="utf-8")):
        name = entry["monster"]["name"]
        for spot in entry["spots"]:
            for spawn in spot["spawns"]:
                names = points.setdefault((spawn["x"], spawn["y"], spawn["z"]), [])
                if name not in names:
                    names.append(name)
    return points


def known_floor_kinds(routes_dir: Path) -> dict[Spot, str]:
    """The floor-change kind of every sqm the saved routes use one on."""
    kinds: dict[Spot, str] = {}
    for path in sorted(routes_dir.glob("*.json")):
        try:
            route = RouteFileLoader.load(path)
        except (RouteError, OSError, ValueError):
            continue  # not a route (or a broken one): nothing to learn
        for wp in route.waypoints:
            if wp.action in FLOOR_KINDS:
                kinds[wp.x, wp.y, wp.z] = wp.action
    return kinds


def load_floor_map(map_dir: Path, routes_dir: Path) -> FloorMap:
    """The floor map of *map_dir*'s walk map, with the kinds the saved routes use."""
    cost = CostMap(map_dir / "cost")
    if cost.empty:
        raise FileNotFoundError(f"no walk map in {map_dir / 'cost'}")
    return FloorMap(cost, known_floor_kinds(routes_dir))


def parse_spot(text: str) -> Spot:
    """``32369,32220,7`` -> (32369, 32220, 7)."""
    x, y, z = (int(v) for v in text.split(","))
    return x, y, z


class FloorMap:
    """The walk map with its floor changes paired across floors."""

    def __init__(self, cost: CostMap, known: dict[Spot, str]) -> None:
        self.cost = cost
        self.known = known
        self.changes_at = cache(self._changes_at)

    def walkable(self, x: int, y: int, z: int) -> bool:
        return self.cost.walkable(x, y, z) and not self.cost.floor_change(x, y, z)

    def _changes_at(self, x: int, y: int, z: int) -> tuple[FloorChange, ...]:
        if not self.cost.floor_change(x, y, z) and (x, y, z) not in self.known:
            return ()
        kind = self.known.get((x, y, z))
        out = []
        for dz in (-1, 1):
            if kind is not None and kind != "stairs" and _FLOOR_DELTA[kind] != dz:
                continue  # a rope or ladder only goes up, a shovel hole down
            to = self._arrival(x, y, z + dz)
            if to is None and kind is not None:
                to = self._nearest_walkable(x, y, z + dz)  # a hole is yellow only once dug
            if to is None:
                continue
            guess = "stairs" if dz > 0 else "ladder"
            out.append(FloorChange((x, y, z), to, kind or guess, kind is not None))
        return tuple(out)

    def _nearest_walkable(self, x: int, y: int, z: int) -> Spot | None:
        for _, nx, ny in _ring(x, y, PAIR_SQM):
            if self.walkable(nx, ny, z):
                return nx, ny, z
        return None

    def _arrival(self, x: int, y: int, z: int) -> Spot | None:
        """The walkable sqm next to the yellow sqm nearest (x, y) on floor z."""
        for _, fx, fy in _ring(x, y, PAIR_SQM):
            if not self.cost.floor_change(fx, fy, z):
                continue
            beside = sorted(
                (max(abs(fx + dx - x), abs(fy + dy - y)), fx + dx, fy + dy)
                for (dx, dy), _ in WALK_STEPS
            )
            for _, ax, ay in beside:
                if self.walkable(ax, ay, z):
                    return ax, ay, z
        return None

    def changes_near(self, x: int, y: int, z: int, radius: int) -> list[FloorChange]:
        """One floor change per patch of yellow sqm within *radius*, nearest first."""
        seen: set[tuple[int, int]] = set()
        out: list[FloorChange] = []
        for _, sx, sy in _ring(x, y, radius):
            if (sx, sy) in seen or not self.cost.floor_change(sx, sy, z):
                continue
            patch: list[tuple[int, int]] = []
            todo = [(sx, sy)]
            seen.add((sx, sy))
            while todo:  # the touching yellow sqm are one staircase or hole
                px, py = todo.pop()
                patch.append((px, py))
                for (dx, dy), _ in WALK_STEPS:
                    n = (px + dx, py + dy)
                    if n not in seen and self.cost.floor_change(*n, z):
                        seen.add(n)
                        todo.append(n)
            for px, py in patch:  # the first sqm of the patch that leads somewhere
                changes = self.changes_at(px, py, z)
                if changes:
                    out.extend(changes)
                    break
        return out


# doc: docs/prs/route-tools.md#route-lint
def route_warnings(route: Route, cost: CostMap) -> list[str]:
    """What a hand-built route gets wrong beyond the legs (the loader checks labels)."""
    out = []
    for i, wp in enumerate(route.waypoints):
        if wp.action in ("stairs", "ladder", "rope") and not cost.floor_change(wp.x, wp.y, wp.z):
            where = f"({wp.x}, {wp.y}, {wp.z})"
            out.append(f"waypoint {i + 1}: {wp.action} on {where}, not a floor change on the map")
    actions = {wp.action for wp in route.waypoints}
    town = actions & set(TOWN_ACTIONS)
    if town and "check" not in actions:
        out.append(f"{', '.join(sorted(town))} but no check: the refill never starts")
    if "check" in actions and not town:
        out.append("a check but no finddepo, deposit, bank or trade: the refill does nothing")
    if "trade" in actions and not route.buy:
        out.append("a trade but an empty buy list: nothing is bought")
    if "deposit" in actions and "finddepo" not in actions:
        out.append("a deposit with no finddepo: no depot is opened")
    return out


def loop_sqm(route: Route) -> int:
    """How many sqm one round of the route walks, floor changes counted as 1."""
    spots = [(wp.x, wp.y, wp.z) for wp in route.waypoints if has_tile(wp)]
    pairs = zip(spots, spots[1:] + spots[:1], strict=True)
    return sum(1 if a[2] != b[2] else max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in pairs)


#: One step of a path: a sqm walked onto, or a floor change taken.
Step = Spot | FloorChange


def find_path(a: Spot, b: Spot, floors: FloorMap, *, margin: int = 60) -> list[Step] | None:
    """The cheapest walk from *a* to *b*, over floors; None when there is none.

    The search stays in the box around *a* and *b* plus *margin* sqm.
    """
    x0, x1 = min(a[0], b[0]) - margin, max(a[0], b[0]) + margin
    y0, y1 = min(a[1], b[1]) - margin, max(a[1], b[1]) + margin

    def guess(s: Spot) -> int:  # never more than the real cost: A* stays exact
        return max(abs(s[0] - b[0]), abs(s[1] - b[1]))

    best = {a: 0}
    came: dict[Spot, tuple[Spot, FloorChange | None]] = {}
    queue = [(guess(a), 0, a)]
    while queue:
        _, cost, here = heapq.heappop(queue)
        if here == b:
            steps: list[Step] = []
            while here != a:
                before, change = came[here]
                steps.append(change if change is not None else here)
                here = before
            return steps[::-1]
        if cost > best[here]:
            continue
        x, y, z = here
        for (dx, dy), step_cost in WALK_STEPS:
            nx, ny = x + dx, y + dy
            if not (x0 <= nx <= x1 and y0 <= ny <= y1):
                continue
            n = (nx, ny, z)
            moves: list[tuple[Spot, int, FloorChange | None]] = []
            if n == b or floors.walkable(*n):
                moves.append((n, step_cost, None))
            else:
                moves += [(c.to, step_cost + CHANGE_COST, c) for c in floors.changes_at(*n)]
            for to, move_cost, change in moves:
                new = cost + move_cost
                if new < best.get(to, new + 1):
                    best[to] = new
                    came[to] = (here, change)
                    heapq.heappush(queue, (new + guess(to), new, to))
    return None
