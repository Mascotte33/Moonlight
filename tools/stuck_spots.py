#!/usr/bin/env python3
"""Count where the cavebot gets stuck, per waypoint, from bot.log.

Usage:
    python tools/stuck_spots.py            # logs/bot.log*, top 15 spots
    python tools/stuck_spots.py --top 30 --since 2026-11-15

A stuck line (``[stuck: <kind>]``) counts against the waypoint the bot was
walking to: the one after the last ``reached waypoint N``. Waypoint numbers
are the GUI's; fix the spot in Route or with ``tools/route.py show``.
"""

# doc: docs/prs/stuck-spots.md#stuck-spots

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

REACHED = re.compile(r"cavebot: reached waypoint (\d+)")
ROUTE = re.compile(r"route '([^']+)'")
STUCK = re.compile(r"\[stuck: ([a-z-]+)\]")
TILE = re.compile(r"(\d{4,5},\d{4,5},\d{1,2})")


class Spot:
    """Stuck events on the way to one waypoint of one route."""

    def __init__(self) -> None:
        self.kinds: Counter[str] = Counter()
        self.tiles: Counter[str] = Counter()

    @property
    def total(self) -> int:
        return sum(self.kinds.values())


def count_stuck_spots(lines: Iterable[str], since: str = "") -> dict[tuple[str, int], Spot]:
    """(route, waypoint walked to) -> its stuck events, from log lines oldest first.

    The route is the last one named (``cavebot: route '...'`` at bot start,
    or a no-move stuck line); logs older than that line carry the route ``?``.
    """
    spots: dict[tuple[str, int], Spot] = defaultdict(Spot)
    route, walking_to = "?", -1
    for line in lines:
        if line[:10] < since:
            continue
        if reached := REACHED.search(line):
            walking_to = int(reached.group(1)) + 1
            continue
        if named := ROUTE.search(line):
            route = named.group(1)
        stuck = STUCK.search(line)
        if stuck is None:
            continue
        spot = spots[(route, walking_to)]
        spot.kinds[stuck.group(1)] += 1
        if tile := TILE.search(line):
            spot.tiles[tile.group(1)] += 1
    return dict(spots)


def log_lines(log_dir: Path) -> Iterable[str]:
    """Lines of ``bot.log.N`` ... ``bot.log``, oldest file first."""

    def rollover_number(path: Path) -> int:
        number = path.suffix[1:]
        return int(number) if number.isdigit() else 0

    files = sorted(log_dir.glob("bot.log*"), key=rollover_number, reverse=True)
    if not files:
        raise FileNotFoundError(f"no bot.log in {log_dir}")
    for path in files:
        yield from path.read_text(encoding="utf-8", errors="replace").splitlines()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    parser.add_argument("--top", type=int, default=15, help="spots to show")
    parser.add_argument("--since", default="", help="only lines from this date on, YYYY-MM-DD")
    args = parser.parse_args()
    spots = count_stuck_spots(log_lines(args.log_dir), args.since)
    ranked = sorted(spots.items(), key=lambda item: -item[1].total)[: args.top]
    print(f"{'route':<24} {'waypoint':>8} {'times':>6}  kinds / most blocked tiles")
    for (route, waypoint), spot in ranked:
        kinds = ", ".join(f"{k} {n}" for k, n in spot.kinds.most_common())
        tiles = " ".join(t for t, _ in spot.tiles.most_common(3))
        shown = "?" if waypoint < 0 else str(waypoint)
        print(f"{route:<24} {shown:>8} {spot.total:>6}  {kinds}" + (f" | {tiles}" if tiles else ""))


if __name__ == "__main__":
    main()
