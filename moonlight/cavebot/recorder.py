"""Route recording helpers for the Cavebot route editor (T8.F1.K1-T8.F4.K1, T3.F9.K1).

Routes are the bot's own files, ``<routes_dir>/<name>.json``, in the format
:class:`RouteFileLoader` reads, so the Cavebot tab can pick and run them.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from moonlight.cavebot.cavebot import (
    NO_TILE,
    RouteError,
    RouteFileLoader,
    Waypoint,
    has_tile,
    label_error,
)
from moonlight.core.state import Position

__all__ = [
    "buy_text",
    "make_waypoint",
    "parse_buy",
    "route_path",
    "StandTracker",
    "save_route",
    "should_add",
    "should_stop_recording",
    "stairs_waypoint",
    "position_line",
    "cursor_line",
]

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")
class StandTracker:
    """The tile the character stood on, not the one it is walking to.

    The minimap re-centers on the destination as soon as a step starts, while
    the character is still on the tile it left. So a position read is only
    trusted once it held for *settle* seconds; until then the tile the
    character left is the confirmed one.
    """

    def __init__(self, settle: float = 0.6, clock: Callable[[], float] = time.monotonic) -> None:
        self._settle = settle
        self._clock = clock
        self._current: Position | None = None
        self._previous: Position | None = None
        self._since = 0.0

    def update(self, pos: Position) -> None:
        """Feed the position read this tick; (0, 0, 0) is no read."""
        if (pos.x, pos.y, pos.z) == (0, 0, 0) or pos == self._current:
            return
        self._previous, self._current = self._current, pos
        self._since = self._clock()

    @property
    def stood(self) -> Position | None:
        """The current tile once it held for *settle* seconds, else the tile left (or None)."""
        if self._current is not None and self._clock() - self._since >= self._settle:
            return self._current
        return self._previous


def make_waypoint(action: str, label: str, live: Position) -> Waypoint:
    """A waypoint for *action* on the character's tile *live*; the bot walks there first.

    Raises:
        RouteError: For a missing label or an unknown position.
    """
    label = label.strip()
    if action == "deposit":  # where finddepo left the bot
        return Waypoint(0, 0, 0, action)
    if action in ("bank", "trade"):
        label = ""
    elif action in NO_TILE:
        if not label:
            raise RouteError(f"{action} needs a label")
        if bad := label_error(action, label):
            raise RouteError(bad)
    if (live.x, live.y, live.z) == (0, 0, 0):
        raise RouteError("Position unknown. Start the bot first.")
    return Waypoint(live.x, live.y, live.z, action, label if action in NO_TILE else "")


def _last_positioned(waypoints: list[Waypoint]) -> Waypoint | None:
    return next((wp for wp in reversed(waypoints) if has_tile(wp)), None)


def should_add(waypoints: list[Waypoint], pos: Position, min_tiles: int) -> bool:
    """True when *pos* is *min_tiles* from the last positioned waypoint or on a new floor."""
    if (pos.x, pos.y, pos.z) == (0, 0, 0):
        return False
    last = _last_positioned(waypoints)
    if last is None or last.z != pos.z:
        return True
    return max(abs(pos.x - last.x), abs(pos.y - last.y)) >= min_tiles


# doc: docs/prs/walking.md#recorded-stairs
def stairs_waypoint(
    waypoints: list[Waypoint], left: Position, arrived: Position
) -> Waypoint | None:
    """The stairs or hole tile walked over from *left* to *arrived* on another floor.

    None on the same floor, after a rope, ladder, shovel or step waypoint
    (that action changed the floor), or without a direction to step in.
    """
    last = _last_positioned(waypoints)
    tools = ("rope", "ladder", "shovel", "step")
    if left.z == arrived.z or (last is not None and last.action in tools):
        return None
    dx = (arrived.x > left.x) - (arrived.x < left.x)
    dy = (arrived.y > left.y) - (arrived.y < left.y)
    if (dx, dy) == (0, 0):
        return None
    return Waypoint(left.x + dx, left.y + dy, left.z, "stairs")


def should_stop_recording(recording: bool, cavebot_on: bool) -> bool:
    """True when a recording is running and the cavebot has started walking a route."""
    return recording and cavebot_on


def position_line(pos: Position, status: str = "") -> str:
    """The character's coordinates and *status*, or "unknown" before the bot reads them."""
    if (pos.x, pos.y, pos.z) == (0, 0, 0):
        return "Position unknown"
    tail = f" ({status})" if status else ""
    return f"Position {pos.x}, {pos.y}, {pos.z}{tail}"


def cursor_line(
    cursor: tuple[int, int] | None, window: tuple[int, int, int, int] | None
) -> str:
    """The mouse in client-window pixels (what a ``drag`` label takes)."""
    if cursor is None or window is None:
        return "Cursor: client not found"
    x, y = cursor[0] - window[0], cursor[1] - window[1]
    if not (0 <= x < window[2] and 0 <= y < window[3]):
        return "Cursor: outside the client"
    return f"Cursor {x} {y}"


# doc: docs/prs/depot.md#trade-waypoint
def parse_buy(text: str) -> list[tuple[str, int]]:
    """The Refill page's buy list: one ``item, percent`` per line, blank lines skipped.

    Raises:
        RouteError: For a line without a 0..100 percent after its last comma.
    """
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        item, _, percent = line.rpartition(",")
        if not item.strip() or not percent.strip().isdigit() or int(percent) > 100:
            raise RouteError(f"buy line {n}: write item, percent (0..100)")
        rows.append((item.strip(), int(percent)))
    return rows


def buy_text(rows: Sequence[tuple[str, int]]) -> str:
    """*rows* as the Refill page shows them (:func:`parse_buy` reads it back)."""
    return "".join(f"{item}, {percent}\n" for item, percent in rows)


def route_path(routes_dir: Path, name: str) -> Path:
    """The file of route *name* in *routes_dir*.

    Raises:
        RouteError: For a name that is not a plain file name.
    """
    if not _NAME_RE.match(name):
        raise RouteError("route name: letters, digits, space, '.', '_' or '-'")
    return Path(routes_dir) / f"{name}.json"


def save_route(
    routes_dir: Path, name: str, waypoints: list[Waypoint], buy: Sequence[tuple[str, int]] = ()
) -> Path:
    """Write *waypoints* (and the *buy* list) as route *name* in *routes_dir*; the file path.

    Raises:
        RouteError: For a bad name or a route the bot would refuse.
    """
    path = route_path(routes_dir, name)
    raw = {
        "name": name,
        "waypoints": [
            {"action": wp.action, "label": wp.label}
            if not has_tile(wp)
            else {"x": wp.x, "y": wp.y, "z": wp.z, "action": wp.action, "label": wp.label}
            if wp.action in (*NO_TILE, "step")
            else {"x": wp.x, "y": wp.y, "z": wp.z, "action": wp.action}
            for wp in waypoints
        ],
        "buy": [{"item": item, "percent": percent} for item, percent in buy],
    }
    RouteFileLoader.parse(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return path
