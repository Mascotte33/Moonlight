"""Routes: waypoints, the route file format and its checks.

A route is a list of :class:`Waypoint` loaded from ``data/waypoints/<route>.json``
by :class:`RouteFileLoader`. The cavebot walks it; the editor, the map and the
route tools read and write it through this module only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from moonlight.perception.floor import MAX_FLOOR, MIN_FLOOR

__all__ = [
    "ACTIONS",
    "HAND_ACTIONS",
    "JUMP_ACTIONS",
    "NO_TILE",
    "WALK_KEYS",
    "Route",
    "RouteError",
    "RouteFileLoader",
    "Waypoint",
    "has_tile",
    "label_error",
    "walked_tiles",
]

#: Valid waypoint actions.
ACTIONS = (
    "walk", "node", "stand", "lure", "rope", "shovel", "ladder", "stairs", "label", "goto", "check",
    "say", "key", "drag", "script", "wait", "step", "finddepo", "boat",
    "deposit", "bank", "trade",
)  # fmt: skip

#: Actions that need no position (executed immediately).
JUMP_ACTIONS = ("label", "goto", "check")
#: Actions done where the bot stands (one command per tick, then a hold).
HAND_ACTIONS = ("say", "key", "drag", "script", "wait", "deposit", "bank", "trade")
#: Hand actions without a label.
_NO_LABEL = ("deposit", "bank", "trade")
#: Actions that may have no tile (x, y, z 0: old routes); added in Route they have one.
NO_TILE = JUMP_ACTIONS + HAND_ACTIONS


# doc: docs/prs/keys-walking.md#arrow-steps
# doc: docs/prs/keys-walking.md#learned-blocks
#: Walk key per one-tile step (y grows downward, w is north); q/e/z/c walk diagonally.
WALK_KEYS = {
    (0, -1): "w", (0, 1): "s", (-1, 0): "a", (1, 0): "d",
    (-1, -1): "q", (1, -1): "e", (-1, 1): "z", (1, 1): "c",
}  # fmt: skip


class RouteError(ValueError):
    """A route file is missing, unreadable or malformed."""


@dataclass(frozen=True)
class Waypoint:
    """One route step. ``label`` names a label / goto target."""

    x: int
    y: int
    z: int
    action: str = "walk"
    label: str = ""


@dataclass
class Route:
    """A named list of waypoints plus the index of the one being executed."""

    name: str
    description: str = ""
    waypoints: list[Waypoint] = field(default_factory=list)
    #: Start over at waypoint 0 after the last one (off: the route completes).
    loop: bool = True
    # doc: docs/prs/depot.md#trade-waypoint
    #: What a ``trade`` buys, in order: (item name, slider percent 0..100).
    buy: list[tuple[str, int]] = field(default_factory=list)
    #: Where the bot is on the route, not part of the route itself.
    current: int = field(default=0, compare=False)

    # doc: docs/prs/route-menu.md#route-name
    def __str__(self) -> str:
        """The name: the GUI's route dropdown shows a route as text.

        The full dataclass text of a long route is ~40000 px wide; the
        dropdown grew to it and Windows cannot place child windows past
        32767 px, so the cavebot page's rows drew on top of each other.
        """
        return self.name


class RouteFileLoader:
    """Load and validate ``data/waypoints/<route>.json`` files."""

    @staticmethod
    def load(path: str | Path) -> Route:
        """Load a route file.

        Raises:
            RouteError: If the file is missing, not JSON, or invalid.
        """
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RouteError(f"cannot read route {path}: {exc}") from exc
        return RouteFileLoader.parse(raw)

    @staticmethod
    def parse(raw: object) -> Route:
        """Validate an already-decoded route object.

        Raises:
            RouteError: On any format violation.
        """
        if not isinstance(raw, dict):
            raise RouteError("route must be a JSON object")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise RouteError("route 'name' must be a non-empty string")
        description = raw.get("description", "")
        if not isinstance(description, str):
            raise RouteError("route 'description' must be a string")
        items = raw.get("waypoints")
        if not isinstance(items, list) or not items:
            raise RouteError("route 'waypoints' must be a non-empty list")

        waypoints = [RouteFileLoader._waypoint(i, item) for i, item in enumerate(items)]
        if not any(has_tile(wp) for wp in waypoints):
            raise RouteError("route needs at least one positional waypoint")
        labels = [wp.label for wp in waypoints if wp.action == "label"]
        if len(labels) != len(set(labels)):
            raise RouteError("duplicate label names")
        for i, wp in enumerate(waypoints):
            if wp.action in ("goto", "check") and wp.label not in labels:
                raise RouteError(f"waypoint {i}: {wp.action} target {wp.label!r} has no label")
        return Route(name=name, description=description, waypoints=waypoints,
                     buy=RouteFileLoader._buy(raw.get("buy", [])))  # fmt: skip

    @staticmethod
    def _buy(items: object) -> list[tuple[str, int]]:
        if not isinstance(items, list):
            raise RouteError("route 'buy' must be a list")
        rows = []
        for i, row in enumerate(items):
            item = row.get("item") if isinstance(row, dict) else None
            percent = row.get("percent") if isinstance(row, dict) else None
            if not isinstance(item, str) or not item.strip():
                raise RouteError(f"buy row {i}: 'item' must be a non-empty string")
            if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
                raise RouteError(f"buy row {i}: 'percent' must be 0..100")
            rows.append((item.strip(), percent))
        return rows

    @staticmethod
    def _waypoint(index: int, item: object) -> Waypoint:
        if not isinstance(item, dict):
            raise RouteError(f"waypoint {index}: must be an object")
        action = item.get("action", "walk")
        if action not in ACTIONS:
            raise RouteError(f"waypoint {index}: action must be one of {ACTIONS}, got {action!r}")
        label = item.get("label", "")
        if action in NO_TILE:
            if not isinstance(label, str) or (not label and action not in _NO_LABEL):
                raise RouteError(f"waypoint {index}: {action} needs a non-empty 'label'")
            if bad := label_error(action, label):
                raise RouteError(f"waypoint {index}: {bad}")
            if "x" not in item:
                return Waypoint(0, 0, 0, action, label)
        coords = []
        for key in ("x", "y", "z"):
            value = item.get(key)
            # bool is an int subclass; reject it explicitly.
            if not isinstance(value, int) or isinstance(value, bool):
                raise RouteError(f"waypoint {index}: {key!r} must be an int")
            coords.append(value)
        if not MIN_FLOOR <= coords[2] <= MAX_FLOOR:
            raise RouteError(f"waypoint {index}: z must be {MIN_FLOOR}..{MAX_FLOOR}")
        if action == "boat" and (bad := label_error(action, label)):
            raise RouteError(f"waypoint {index}: {bad}")
        if action not in (*NO_TILE, "step", "boat"):
            label = ""
        elif action == "step" and label not in WALK_KEYS.values():
            keys = list(WALK_KEYS.values())
            raise RouteError(f"waypoint {index}: step label must be one of {keys}")
        return Waypoint(coords[0], coords[1], coords[2], action, label)


def label_error(action: str, label: str) -> str | None:
    """Why *label* cannot be the label of a hand action (``say``, ``wait``...), or None."""
    if action == "say" and not re.fullmatch(r"[A-Za-z0-9 ]+", label):
        return "say text is letters, digits and spaces"
    if action == "drag" and not re.fullmatch(r"\d+ \d+ \d+ \d+", label):
        return "drag label is four numbers: x1 y1 x2 y2"
    if action == "key" and any(ch.isspace() for ch in label):
        return f"key {label!r} has a space"
    if action == "wait" and not re.fullmatch(r"\d+(\.\d+)?", label):
        return "wait label is seconds"
    if action == "boat" and not re.fullmatch(r"[A-Za-z][A-Za-z ]*", label):
        return "boat label is the destination said to the captain: letters and spaces"
    if action == "script" and not re.fullmatch(r"\w+", label):
        return "script label is a file name in scripts/ without .py"
    return None


def has_tile(wp: Waypoint) -> bool:
    """True when the bot walks to *wp*'s tile before doing it."""
    return wp.action not in NO_TILE or (wp.x, wp.y, wp.z) != (0, 0, 0)


# doc: docs/prs/route-trust.md#walked-lines
def walked_tiles(waypoints: list[Waypoint], loop: bool) -> set[tuple[int, int, int]]:
    """Tiles on the straight line between neighbouring waypoints on one floor.

    A route is recorded by walking it, so these were walked; the client's
    cost map can still call one a wall (the Thais sewer row y 32218, 2026-11-01:
    the bot skipped 10 waypoints). A real wall is still found by bumping it.
    """
    tiles: set[tuple[int, int, int]] = set()
    spots = [(wp.x, wp.y, wp.z) for wp in waypoints if has_tile(wp)]
    pairs = list(zip(spots, spots[1:], strict=False))
    if loop and len(spots) > 2:
        pairs.append((spots[-1], spots[0]))
    for (x0, y0, z0), (x1, y1, z1) in pairs:
        if z0 != z1:
            continue
        n = max(abs(x1 - x0), abs(y1 - y0), 1)  # two waypoints on one tile: n would be 0
        for i in range(n + 1):
            tiles.add((round(x0 + (x1 - x0) * i / n), round(y0 + (y1 - y0) * i / n), z0))
    return tiles
