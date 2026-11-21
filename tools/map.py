#!/usr/bin/env python3
"""Look at the game map and check a route's legs, for building routes by hand.

Usage:
    python tools/map.py show MountSternumUndeads          # the route's area as a PNG
    python tools/map.py show MountSternumUndeads --floor 8
    python tools/map.py area 32369,32241,7 --size 80        # any area, no route
    python tools/map.py check MountSternumUndeads           # legs with no walkable path

The PNG goes to ``logs/map_<name>.png`` (``--out`` to change), 1 sqm =
``--scale`` px. Dots are waypoints (numbers as in the GUI, 1-based), lines
join them in order, red lines are legs the walk map finds no path for, blue
triangles point to the next waypoint's floor. ``check`` exits 1 on a bad leg.

# doc: docs/prs/map.md#map-tool
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

import cv2

from moonlight.cavebot.cavebot import RouteFileLoader, Waypoint, has_tile
from moonlight.cavebot.route_map import MapTiles, Spot, bad_legs, draw_route, leg_checker
from moonlight.core.wiring import MAP_DIR, walk_map_walkable
from moonlight.gui.loop_factory import load_real_config

_MARGIN = 10


def _route(name: str) -> list[Waypoint]:
    return RouteFileLoader.load(load_real_config().routes_dir / f"{name}.json").waypoints


def _leg_ok() -> Callable[[Spot, Spot], bool]:
    walkable = walk_map_walkable(MAP_DIR)
    if walkable is None:
        sys.exit(f"no walk map in {MAP_DIR / 'cost'}")
    return leg_checker(walkable)


def _save(name: str, x0: int, y0: int, z: int, w: int, h: int, args: argparse.Namespace,
          waypoints: list[Waypoint], bad: list[tuple[int, int]]) -> None:  # fmt: skip
    area = MapTiles(MAP_DIR).area(x0, y0, z, w, h)
    image = draw_route(area, (x0, y0), z, args.scale, waypoints, bad)
    out = Path(args.out or f"logs/map_{name}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    print(f"{out}: x {x0}..{x0 + w - 1}, y {y0}..{y0 + h - 1}, floor {z}, {args.scale} px per sqm")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("show", "area", "check"))
    parser.add_argument("target", help="route name (show, check) or x,y,z (area)")
    parser.add_argument("--floor", type=int, help="show: the floor (default: the first waypoint's)")
    parser.add_argument("--size", type=int, default=60, help="area: sqm across and down")
    parser.add_argument("--scale", type=int, default=8, help="px per sqm")
    parser.add_argument("--out", help="the PNG path")
    args = parser.parse_args()
    if args.command == "area":
        x, y, z = (int(v) for v in args.target.split(","))
        half = args.size // 2
        name = f"{x}_{y}_{z}"
        _save(name, x - half, y - half, z, args.size, args.size, args, [], [])
        return
    waypoints = _route(args.target)
    leg_ok = _leg_ok()
    bad = bad_legs(waypoints, leg_ok)
    if args.command == "check":
        for i, j in bad:
            a, b = waypoints[i], waypoints[j]
            print(f"leg {i + 1} -> {j + 1}: ({a.x}, {a.y}, {a.z}) -> ({b.x}, {b.y}, {b.z}) no path")
        print(f"{len(bad)} bad legs")
        sys.exit(1 if bad else 0)
    spots = [wp for wp in waypoints if has_tile(wp)]
    z = args.floor if args.floor is not None else spots[0].z
    on_floor = [wp for wp in spots if wp.z == z] or sys.exit(f"no waypoint on floor {z}")
    x0 = min(wp.x for wp in on_floor) - _MARGIN
    y0 = min(wp.y for wp in on_floor) - _MARGIN
    w = max(wp.x for wp in on_floor) + _MARGIN - x0 + 1
    h = max(wp.y for wp in on_floor) + _MARGIN - y0 + 1
    _save(args.target, x0, y0, z, w, h, args, waypoints, bad)


if __name__ == "__main__":
    main()
