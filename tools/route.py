#!/usr/bin/env python3
"""Show, move or delete waypoints of a route file by their GUI numbers.

Usage:
    python tools/route.py show MountSternumUndeads 150-160
    python tools/route.py delete MountSternumUndeads 156-238
    python tools/route.py move MountSternumUndeads 199-335 --after 154
    python tools/route.py insert MountSternumUndeads 0 < waypoints.txt
    python tools/route.py new MyHunt < waypoints.txt
    python tools/route.py path 32369,32220,7 32400,32190,8 > waypoints.txt

``new`` writes a new route from stdin, one waypoint per line:
``x y z [action [label]]`` or, for a waypoint with no sqm, ``action label``.
It refuses a name that already exists. ``insert NAME N`` puts lines in the
same format before waypoint N of an existing route.

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

``block travel --depot D`` prints point 0, the start of a route: ``script
go_to_city`` (jumps to ``From<City>`` of the depot you stand in), then for
each city in ``CITY_DEPOTS`` the walk from its depot to D and ``goto Depot``.
D's own city just goes to ``Depot``. A city with no path (an island no saved
route sails to yet) is left out and named on stderr. A path uses the door
scripts and boat talks the saved routes already make (``route_links``).

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
from moonlight.cavebot.recorder import route_path, save_route, waypoint_json
from moonlight.cavebot.route_plan import (
    CITY_DEPOTS,
    FloorChange,
    FloorMap,
    Link,
    Step,
    find_path,
    load_floor_map,
    nearest_city,
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
    waypoints = _read_waypoints()
    path = save_route(routes_dir, name, waypoints)  # raises on a route the bot refuses
    print(f"{path}: {len(waypoints)} waypoints")


def _read_waypoints() -> list[Waypoint]:
    """``new``'s lines from stdin."""
    waypoints = []
    for line in sys.stdin.read().splitlines():
        text = line.partition("#")[0].strip()
        words = text.split(maxsplit=4)
        if not words:
            continue
        if words[0].lstrip("-").isdigit():
            x, y, z = (int(w) for w in words[:3])
            waypoints.append(Waypoint(x, y, z, *words[3:5]))
        else:
            action, _, label = text.partition(" ")
            waypoints.append(Waypoint(0, 0, 0, action, label.strip()))
    return waypoints


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


# doc: docs/prs/city-travel.md#travel-block
def _travel_block(args: argparse.Namespace) -> None:
    """Point 0 in ``new``'s format: one ``From<City>`` block per city depot."""
    if args.depot is None:
        sys.exit("block travel needs --depot x,y,z (the route's depot sqm)")
    goal = parse_spot(args.depot)
    home = nearest_city(*goal)
    if home is None:
        sys.exit(f"{args.depot} is in no city of CITY_DEPOTS")
    floors = load_floor_map(MAP_DIR, load_real_config().routes_dir)
    print("script go_to_city")
    print(f"label From{home}")
    print("goto Depot")
    for city, depot in CITY_DEPOTS.items():
        if city == home:
            continue
        # A town exit (Kazordoon's doors) can be 100+ sqm off the line between depots.
        steps = find_path(depot, goal, floors, margin=150)
        if steps is None:
            print(f"no path from {city}: left out", file=sys.stderr)
            continue
        print(f"label From{city}")
        _print_steps(steps, args.every)
        print("goto Depot")


# doc: docs/prs/route-tools.md#path
def _print_path(floors: FloorMap, start: str, goal: str, every_sqm: int) -> None:
    steps = find_path(parse_spot(start), parse_spot(goal), floors)
    if steps is None:
        sys.exit(f"no path from {start} to {goal} on the walk map")
    _print_steps(steps, every_sqm)


def _print_steps(steps: list[Step], every_sqm: int) -> None:
    walked = 0
    for i, step in enumerate(steps):
        if isinstance(step, Link):
            print(*step.at, "stand")
            print(*step.actions, sep="\n")
            walked = 0
            continue
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
    parser.add_argument("command", choices=("show", "delete", "move", "insert", "new", "path", "block"))
    parser.add_argument(
        "route",
        help="file name in data/waypoints, without .json (path: A as x,y,z; block: refill, travel)",
    )
    parser.add_argument("waypoints", nargs="?", help="N or N-M (path: B; block: the check sqm)")
    parser.add_argument("--after", type=int, help="move: the waypoint the block goes after")
    parser.add_argument("--every", type=int, default=8, help="path, block: sqm between walks")
    parser.add_argument("--depot", help="block: x,y,z to look for a free depot from (travel: the route's depot)")
    parser.add_argument("--bank", help="block: x,y,z next to the banker")
    parser.add_argument("--shop", help="block: x,y,z next to the shop NPC")
    parser.add_argument("--hunt-label", default="StartHunt", help="block: the hunt loop's label")
    args = parser.parse_args()
    if args.command == "new":
        _new(args.route)
        return
    if args.command == "block" and args.route == "travel":
        _travel_block(args)
        return
    if args.waypoints is None:
        parser.error(f"{args.command} needs a second argument")
    if args.command == "path":
        _path(args)
        return
    if args.command == "block":
        if args.route != "refill":
            parser.error("the blocks are refill and travel")
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
    if args.command == "insert":
        new = [waypoint_json(wp) for wp in _read_waypoints()]
        rest = waypoints[:first] + new + waypoints[first:]
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
