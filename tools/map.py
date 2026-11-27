#!/usr/bin/env python3
"""Look at the game map and check a route's legs, for building routes by hand.

Usage:
    python tools/map.py show MountSternumUndeads          # the route's area as a PNG
    python tools/map.py show MountSternumUndeads --floor 8
    python tools/map.py area 32369,32241,7 --size 80        # any area, spot marked
    python tools/map.py check MountSternumUndeads           # legs with no walkable path
    python tools/map.py check --all                         # every route in data/waypoints
    python tools/map.py stairs 32369,32241,7 --size 20      # floor changes near a sqm
    python tools/map.py find depot --near 32369,32241,7     # client markers + route town spots
    python tools/map.py monsters Troll                      # where a monster spawns
    python tools/map.py refresh                             # download data/spawns.json + data/monsters/ again

The PNG goes to ``logs/map_<name>.png`` (``--out`` to change), 1 sqm =
``--scale`` px. Dots are waypoints (numbers as in the GUI, 1-based), lines
join them in order, red lines are legs the walk map finds no path for, blue
triangles point to the next waypoint's floor. ``check`` exits 1 on a bad leg
or a warning: a walk on a wall, a floor change the map lacks, ring and
targeting not switched on only in the hunt, a refill missing a part.

# doc: docs/prs/map.md#map-tool
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

from moonlight.cavebot.route import Route, RouteFileLoader, Waypoint, has_tile
from moonlight.cavebot.floor_changes import load_confirmed
from moonlight.cavebot.pathing import CostMap
from moonlight.cavebot.route_map import (
    LegPath,
    MapTiles,
    Spot,
    bad_legs,
    draw_route,
    leg_path,
)
from moonlight.cavebot.route_plan import (
    PICTURES_DIR,
    SPAWNS_FILE,
    download_pictures,
    download_spawns,
    load_floor_map,
    loop_sqm,
    monster_spots,
    parse_spot,
    read_markers,
    route_places,
    route_warnings,
)
from moonlight.core.wiring import CLIENT_MINIMAP_DIR, MAP_DIR, walk_map_walkable
from moonlight.gui.loop_factory import load_real_config

_MARGIN = 10

def _route(name: str) -> Route:
    return RouteFileLoader.load(load_real_config().routes_dir / f"{name}.json")


def _leg_path() -> LegPath:
    walkable = walk_map_walkable(MAP_DIR)
    if walkable is None:
        sys.exit(f"no walk map in {MAP_DIR / 'cost'}")
    return leg_path(walkable)


def _save(name: str, x0: int, y0: int, z: int, w: int, h: int, args: argparse.Namespace,
          waypoints: list[Waypoint], bad: list[tuple[int, int]],
          path: LegPath | None = None, me: Spot | None = None) -> None:  # fmt: skip
    area = MapTiles(MAP_DIR).area(x0, y0, z, w, h)
    image = draw_route(area, (x0, y0), z, args.scale, waypoints, bad, path=path, me=me)
    out = Path(args.out or f"logs/map_{name}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    print(f"{out}: x {x0}..{x0 + w - 1}, y {y0}..{y0 + h - 1}, floor {z}, {args.scale} px per sqm")


# doc: docs/prs/route-tools.md#floor-changes
def _stairs(spot: Spot, radius: int) -> None:
    floors = load_floor_map(MAP_DIR, load_real_config().routes_dir)
    changes = floors.changes_near(*spot, radius)
    for change in changes:
        x, y, z = change.at
        where = "up" if change.to[2] < z else "down"
        how = "known" if change.known else "guess"
        print(f"{x} {y} {z} {change.kind}  ({how}) -> {where} to {change.to}")
    print(f"{len(changes)} floor changes within {radius} sqm")


# doc: docs/prs/route-tools.md#find
def _find(text: str, near: Spot | None) -> None:
    markers = CLIENT_MINIMAP_DIR / "minimapmarkers.bin"
    places = (
        [(spot, f"marker: {name}") for spot, name in read_markers(markers)]
        if markers.exists()
        else []
    )
    places += route_places(load_real_config().routes_dir)
    hits = [(spot, name) for spot, name in places if text.lower() in name.lower()]
    if near is not None:
        hits.sort(
            key=lambda hit: (
                hit[0][2] != near[2],
                max(abs(hit[0][0] - near[0]), abs(hit[0][1] - near[1])),
            )
        )
    for (x, y, z), name in hits:
        print(f"{x} {y} {z}  {name}")
    print(f"{len(hits)} places match {text!r}")


# doc: docs/prs/route-tools.md#monsters
def _monsters(name: str) -> None:
    if not SPAWNS_FILE.exists():
        sys.exit(f"no {SPAWNS_FILE}: run python tools/map.py refresh")
    spots = monster_spots(SPAWNS_FILE, name)
    for spot in spots:
        x, y, z = spot.centre
        print(
            f"{x} {y} {z}  {spot.spawns:4} spawns  {spot.place}{'  (premium)' if spot.premium else ''}"
        )
    print(f"{len(spots)} places with {name}")


def _check(name: str) -> bool:
    """Print the route's bad legs and warnings; True when it has none."""
    route = _route(name)
    bad = bad_legs(route.waypoints, _leg_path())
    print(f"== {name}")
    for i, j in bad:
        a, b = route.waypoints[i], route.waypoints[j]
        print(f"leg {i + 1} -> {j + 1}: ({a.x}, {a.y}, {a.z}) -> ({b.x}, {b.y}, {b.z}) no path")
    warnings = route_warnings(route, CostMap(MAP_DIR / "cost"), load_confirmed())
    for warning in warnings:
        print(warning)
    print(f"{len(bad)} bad legs, {len(warnings)} warnings, one round {loop_sqm(route)} sqm")
    return not bad and not warnings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command", choices=("show", "area", "check", "stairs", "find", "monsters", "refresh")
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="route name (show, check), x,y,z (area, stairs), text (find), monster (monsters)",
    )
    parser.add_argument("--near", help="find: nearest to this x,y,z first")
    parser.add_argument("--floor", type=int, help="show: the floor (default: the first waypoint's)")
    parser.add_argument("--size", type=int, default=60, help="area: sqm across; stairs: radius")
    parser.add_argument("--scale", type=int, default=8, help="px per sqm")
    parser.add_argument("--out", help="the PNG path")
    parser.add_argument("--all", action="store_true", help="check: every route")
    args = parser.parse_args()
    if args.command == "refresh":
        print(f"{SPAWNS_FILE}: {download_spawns(SPAWNS_FILE)} monsters")
        print(f"{PICTURES_DIR}: {download_pictures(SPAWNS_FILE, PICTURES_DIR)} new pictures")
        return
    if args.command == "check" and args.all:
        names = sorted(p.stem for p in load_real_config().routes_dir.glob("*.json"))
        failed = [name for name in names if not _check(name)]
        print(f"{len(names) - len(failed)} of {len(names)} routes clean; check again: {failed}")
        sys.exit(1 if failed else 0)
    if args.target is None:
        parser.error(f"{args.command} needs a target")
    if args.command == "find":
        _find(args.target, parse_spot(args.near) if args.near else None)
        return
    if args.command == "monsters":
        _monsters(args.target)
        return
    if args.command == "stairs":
        _stairs(parse_spot(args.target), args.size)
        return
    if args.command == "area":
        x, y, z = parse_spot(args.target)
        half = args.size // 2
        name = f"{x}_{y}_{z}"
        _save(name, x - half, y - half, z, args.size, args.size, args, [], [], me=(x, y, z))
        return
    if args.command == "check":
        sys.exit(0 if _check(args.target) else 1)
    route = _route(args.target)
    waypoints = route.waypoints
    path = _leg_path()
    bad = bad_legs(waypoints, path)
    spots = [wp for wp in waypoints if has_tile(wp)]
    z = args.floor if args.floor is not None else spots[0].z
    on_floor = [wp for wp in spots if wp.z == z] or sys.exit(f"no waypoint on floor {z}")
    x0 = min(wp.x for wp in on_floor) - _MARGIN
    y0 = min(wp.y for wp in on_floor) - _MARGIN
    w = max(wp.x for wp in on_floor) + _MARGIN - x0 + 1
    h = max(wp.y for wp in on_floor) + _MARGIN - y0 + 1
    _save(args.target, x0, y0, z, w, h, args, waypoints, bad, path)


if __name__ == "__main__":
    main()
