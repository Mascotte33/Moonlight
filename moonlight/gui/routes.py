"""Waypoint route files — data layer for the Recorder tab and (later) T22.

A *route* is an ordered list of waypoints captured from live play (Recorder
tab) that the cavebot engine (T22, pending) will eventually follow. This
module is pure data: it defines the on-disk JSON schema and validates
it. It deliberately contains **no pathfinding** — that is T22's job.

On-disk schema (``data/routes/<name>.json``)
--------------------------------------------

.. code-block:: json

    {
      "name": "cave1",
      "created_at": 1697000000.0,
      "waypoints": [
        {"type": "walk",   "x": 312, "y": 310, "ts": 1697000000.123},
        {"type": "ladder", "x": 312, "y": 244, "ts": 1697000001.500},
        {"type": "rope",   "x": 300, "y": 200, "ts": 1697000002.750},
        {"type": "stairs", "x": 290, "y": 150, "ts": 1697000004.000}
      ]
    }

Fields
------
* ``name`` — route name; also the file name stem.
* ``created_at`` — POSIX timestamp of the first recorded waypoint.
* ``waypoints`` — ordered list; each entry:
    - ``type`` — one of :data:`WAYPOINT_TYPES` (``walk``/``ladder``/
      ``rope``/``stairs``); the movement action the cavebot should use
      to reach the next point.
    - ``x`` / ``y`` — target game-map coordinates (int).
    - ``ts`` — POSIX timestamp when the waypoint was recorded (float).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from moonlight.gui.config_bridge import repo_root

__all__ = [
    "WAYPOINT_TYPES",
    "Route",
    "Waypoint",
    "default_routes_dir",
    "list_routes",
    "load_route",
    "route_path",
    "save_route",
]

#: Valid waypoint movement types (the actions T22 will execute).
WAYPOINT_TYPES: tuple[str, ...] = ("walk", "ladder", "rope", "stairs")

#: Route/file names: conservative so paths stay portable.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class Waypoint:
    """One recorded waypoint: a movement *type* to a map coordinate."""

    type: str
    x: int
    y: int
    ts: float

    def validate(self) -> Waypoint:
        """Raise ``ValueError`` on invalid values; return self otherwise."""
        if self.type not in WAYPOINT_TYPES:
            raise ValueError(
                f"waypoint type must be one of {WAYPOINT_TYPES}, got {self.type!r}"
            )
        if not isinstance(self.x, int) or isinstance(self.x, bool):
            raise ValueError(f"waypoint x must be int, got {self.x!r}")
        if not isinstance(self.y, int) or isinstance(self.y, bool):
            raise ValueError(f"waypoint y must be int, got {self.y!r}")
        if not isinstance(self.ts, (int, float)):
            raise ValueError(f"waypoint ts must be a number, got {self.ts!r}")
        return self

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the schema's dict form."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Waypoint:
        """Build (and validate) a waypoint from a dict."""
        if not isinstance(data, dict):
            raise ValueError(f"waypoint must be an object, got {type(data).__name__}")
        wp = cls(
            type=str(data.get("type", "")),
            x=int(data["x"]),
            y=int(data["y"]),
            ts=float(data["ts"]),
        )
        return wp.validate()


@dataclass
class Route:
    """A named, ordered waypoint route (cavebot path, T22's input)."""

    name: str
    created_at: float
    waypoints: list[Waypoint] = None  # type: ignore[assignment]

    def __init__(
        self, name: str, created_at: float, waypoints: list[Waypoint] | None = None
    ) -> None:
        self.name = name
        self.created_at = float(created_at)
        self.waypoints: list[Waypoint] = list(waypoints) if waypoints else []

    def validate(self) -> Route:
        """Raise ``ValueError`` on invalid values; return self otherwise."""
        if not _NAME_RE.match(self.name):
            raise ValueError(
                f"route name must match {_NAME_RE.pattern}, got {self.name!r}"
            )
        for i, wp in enumerate(self.waypoints):
            try:
                wp.validate()
            except ValueError as exc:
                raise ValueError(f"waypoint[{i}]: {exc}") from None
        return self

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the documented JSON schema."""
        return {
            "name": self.name,
            "created_at": self.created_at,
            "waypoints": [wp.to_dict() for wp in self.waypoints],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Route:
        """Build (and validate) a route from a dict."""
        if not isinstance(data, dict):
            raise ValueError(f"route must be an object, got {type(data).__name__}")
        raw_wps = data.get("waypoints", [])
        if not isinstance(raw_wps, list):
            raise ValueError("waypoints must be a list")
        route = cls(
            name=str(data.get("name", "")),
            created_at=float(data.get("created_at", 0.0)),
            waypoints=[Waypoint.from_dict(wp) for wp in raw_wps],
        )
        return route.validate()

    def append(self, waypoint: Waypoint) -> None:
        """Append a validated waypoint (Recorder tab hook)."""
        self.waypoints.append(waypoint.validate())


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def default_routes_dir() -> Path:
    """The default routes directory: ``<repo>/data/routes``."""
    return repo_root() / "data" / "routes"


def route_path(name: str, directory: Path | str | None = None) -> Path:
    """Path for route *name* (``<dir>/<name>.json``); validates the name."""
    if not _NAME_RE.match(name):
        raise ValueError(f"route name must match {_NAME_RE.pattern}, got {name!r}")
    base = Path(directory) if directory is not None else default_routes_dir()
    return base / f"{name}.json"


def save_route(route: Route, directory: Path | str | None = None) -> Path:
    """Validate *route* and write it to ``data/routes/<name>.json``.

    Creates the directory if needed. Returns the written path.
    """
    path = route_path(route.name, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(route.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def load_route(path: Path | str) -> Route:
    """Load and validate a route from *path*.

    Raises ``ValueError`` (bad schema) or ``OSError`` (missing file).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Route.from_dict(data)


def list_routes(directory: Path | str | None = None) -> list[str]:
    """Sorted route names available under *directory* (empty if absent)."""
    base = Path(directory) if directory is not None else default_routes_dir()
    if not base.is_dir():
        return []
    names = [p.stem for p in base.glob("*.json") if _NAME_RE.match(p.stem)]
    return sorted(names)


def now_ts() -> float:
    """Current POSIX timestamp (helper for recorders/tests)."""
    return time.time()
