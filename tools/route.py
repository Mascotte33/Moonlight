#!/usr/bin/env python3
"""Show, move or delete waypoints of a route file by their GUI numbers.

Usage:
    python tools/route.py show MountSternumUndeads 150-160
    python tools/route.py delete MountSternumUndeads 156-238
    python tools/route.py move MountSternumUndeads 199-335 --after 154

Numbers are the GUI's (0-based) and ranges include both ends. An edit keeps
the old file as ``<route>.json.bak``, checks the new route with the bot's own
loader and then the bot's config (a start waypoint past the end shows there).
"""

from __future__ import annotations

import argparse
import json
import shutil

from moonlight.cavebot.cavebot import RouteFileLoader
from moonlight.gui.loop_factory import load_real_config


def _range(text: str) -> tuple[int, int]:
    first, _, last = text.partition("-")
    return int(first), int(last or first)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("show", "delete", "move"))
    parser.add_argument("route", help="file name in data/waypoints, without .json")
    parser.add_argument("waypoints", type=_range, help="N or N-M")
    parser.add_argument("--after", type=int, help="move: the waypoint the block goes after")
    args = parser.parse_args()
    path = load_real_config().routes_dir / f"{args.route}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    waypoints = raw["waypoints"]
    first, last = args.waypoints
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
