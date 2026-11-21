#!/usr/bin/env python3
"""Show, move or delete waypoints of a route file by their GUI numbers.

Usage:
    python tools/route.py show MountSternumUndeads 150-160
    python tools/route.py delete MountSternumUndeads 156-238
    python tools/route.py move MountSternumUndeads 199-335 --after 154
    python tools/route.py new MyHunt < waypoints.txt
    python tools/route.py path 32369,32220,7 32400,32190,8 > waypoints.txt

``new`` writes a new route from stdin, one waypoint per line:
``x y z [action [label]]`` or, for a waypoint with no sqm, ``action label``.
It refuses a name that already exists.

``path A B`` prints the walk from A to B in ``new``'s format: a walk every
``--every`` sqm and each floor change. A floor change's kind comes from a
route that already uses that sqm, else it is a guess, marked ``# guess``
(down = stairs, up = ladder; a rope spot shows as ladder: fix it by hand).

``block refill CHECK_SQM --depot D [--bank B] [--shop S]`` prints the refill
part of a hunt: ``check GoToRefill``, ``goto StartHunt``, ``label
GoToRefill``, the walk to the depot (``finddepo``, ``deposit``), the bank
(``bank``), the shop (``trade``) and back to CHECK_SQM, then ``goto
StartHunt``. Paste it at the end of the hunt loop; the hunt must start with
``label StartHunt`` (``--hunt-label``) and the route needs its ``buy`` list.

Numbers are the GUI's (0-based) and ranges include both ends. An edit keeps
the old file as ``<route>.json.bak``, checks the new route with the bot's own
loader and then the bot's config (a start waypoint past the end shows there).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys

from moonlight.cavebot.cavebot import RouteFileLoader, Waypoint
from moonlight.cavebot.recorder import route_path, save_route
from moonlight.cavebot.route_plan import (
    FloorChange,
    FloorMap,
    find_path,
    load_floor_map,
    parse_spot,
)
from moonlight.core.wiring import MAP_DIR
from moonlight.gui.loop_factory import load_real_config


def _range(text: str) -> tuple[int, int]:
    first, _, last = text.partition("-")
    return int(first), int(last or first)


def _new(name: str) -> None:
    routes_dir = load_real_config().routes_dir
    if route_path(routes_dir, name).exists():
        sys.exit(f"route {name!r} already exists; pick another name")
    waypoints = []
    for line in sys.stdin.read().splitlines():
        words = line.partition("#")[0].strip().split(maxsplit=4)
        if not words:
            continue
        if words[0].lstrip("-").isdigit():
            x, y, z = (int(w) for w in words[:3])
            waypoints.append(Waypoint(x, y, z, *words[3:5]))
        else:
            action, _, label = line.partition("#")[0].strip().partition(" ")
            waypoints.append(Waypoint(0, 0, 0, action, label.strip()))
    path = save_route(routes_dir, name, waypoints)  # raises on a route the bot refuses
    print(f"{path}: {len(waypoints)} waypoints")


def _path(args: argparse.Namespace) -> None:
    floors = load_floor_map(MAP_DIR, load_real_config().routes_dir)
    _print_path(floors, args.route, args.waypoints, args.every)


# doc: docs/prs/route-tools.md#refill-block
def _refill_block(args: argparse.Namespace) -> None:
    """The refill part of a hunt, in ``new``'s format, from the hunt's check sqm."""
    if args.depot is None:
        sys.exit("block refill needs --depot x,y,z (the sqm to look for a free depot from)")
    floors = load_floor_map(MAP_DIR, load_real_config().routes_dir)
    print("check GoToRefill")
    print(f"goto {args.hunt_label}")
    print("label GoToRefill")
    here = args.waypoints
    for stop, actions in (
        (args.depot, ("finddepo", "deposit")),
        (args.bank, ("bank",)),
        (args.shop, ("trade",)),
    ):
        if stop is None:
            continue
        _print_path(floors, here, stop, args.every)
        x, y, z = parse_spot(stop)
        for action in actions:
            print(f"{x} {y} {z} {action}")
        here = stop
    _print_path(floors, here, args.waypoints, args.every)
    print(f"goto {args.hunt_label}")


# doc: docs/prs/route-tools.md#path
def _print_path(floors: FloorMap, start: str, goal: str, every_sqm: int) -> None:
    steps = find_path(parse_spot(start), parse_spot(goal), floors)
    if steps is None:
        sys.exit(f"no path from {start} to {goal} on the walk map")
    walked = 0
    for i, step in enumerate(steps):
        if isinstance(step, FloorChange):
            x, y, z = step.at
            note = "" if step.known else f"  # guess, leads to {step.to}"
            print(f"{x} {y} {z} {step.kind}{note}")
            walked = 0
            continue
        walked += 1
        if walked >= every_sqm or i == len(steps) - 1:
            print(*step)
            walked = 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("show", "delete", "move", "new", "path", "block"))
    parser.add_argument(
        "route", help="file name in data/waypoints, without .json (path: A as x,y,z; block: refill)"
    )
    parser.add_argument("waypoints", nargs="?", help="N or N-M (path: B; block: the check sqm)")
    parser.add_argument("--after", type=int, help="move: the waypoint the block goes after")
    parser.add_argument("--every", type=int, default=8, help="path, block: sqm between walks")
    parser.add_argument("--depot", help="block: x,y,z to look for a free depot from")
    parser.add_argument("--bank", help="block: x,y,z next to the banker")
    parser.add_argument("--shop", help="block: x,y,z next to the shop NPC")
    parser.add_argument("--hunt-label", default="StartHunt", help="block: the hunt loop's label")
    args = parser.parse_args()
    if args.command == "new":
        _new(args.route)
        return
    if args.waypoints is None:
        parser.error(f"{args.command} needs a second argument")
    if args.command == "path":
        _path(args)
        return
    if args.command == "block":
        if args.route != "refill":
            parser.error("the only block is refill")
        _refill_block(args)
        return
    path = load_real_config().routes_dir / f"{args.route}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    waypoints = raw["waypoints"]
    first, last = _range(args.waypoints)
    block = waypoints[first : last + 1]
    if args.command == "show":
        for index, waypoint in enumerate(block, first):
            print(index, waypoint)
        return
    rest = waypoints[:first] + waypoints[last + 1 :]
    if args.command == "move":
        if args.after is None or first <= args.after <= last:
            parser.error("move needs --after, outside the moved block")
        after = args.after if args.after < first else args.after - len(block)
        rest[after + 1 : after + 1] = block
    raw["waypoints"] = rest
    RouteFileLoader.parse(raw)  # raises on a broken route, before the file is touched
    shutil.copy(path, path.with_suffix(".json.bak"))
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    print(f"{args.command} {first}-{last}: {len(waypoints)} -> {len(rest)} waypoints")
    load_real_config()  # raises when the config's start waypoint is now past the end


if __name__ == "__main__":
    main()
