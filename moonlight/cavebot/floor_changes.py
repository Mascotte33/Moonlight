"""Floor changes confirmed in the game: ``data/floor_changes.csv``.

The walk map shows where the floor changes (a yellow sqm), not which way or
how (walk onto it, use it, rope, dig). A guess put into a route once took a
sewer grate for stairs up. So only what the bot itself did counts as known:
each time a route's floor change (``stairs``, ``ladder``, ``rope``,
``shovel``, ``step``) lands on the floor the route expects next, the cavebot
adds ``x,y,z,kind,to_z`` here. ``map.py check`` warns about any floor change
of a route that is not listed (watch it on its first run), and the path
finder takes only listed ones as known.

Kind ``hole`` is a hole the map does not show (the owner names it): the
walker never steps on it (Tainted Caves entrance 32697,31950,7, 2026-11-26).

# doc: docs/prs/floor-rules.md#confirmed
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

__all__ = ["FLOOR_CHANGES_FILE", "FLOOR_KIND_ACTIONS", "load_confirmed", "record_confirmed"]

log = logging.getLogger(__name__)

FLOOR_CHANGES_FILE = Path("data") / "floor_changes.csv"
#: Waypoint actions that change the floor.
FLOOR_KIND_ACTIONS = ("stairs", "ladder", "rope", "shovel", "step")
_HEADER = "x,y,z,kind,to_z\n"
_lock = threading.Lock()

Spot = tuple[int, int, int]


def load_confirmed(path: Path | None = None) -> dict[tuple[Spot, str], int]:
    """``((x, y, z), kind) -> floor it leads to`` for each confirmed floor change."""
    path = FLOOR_CHANGES_FILE if path is None else path
    out: dict[tuple[Spot, str], int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) != 5:
            continue
        try:
            x, y, z, to_z = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[4])
        except ValueError:
            continue
        out[(x, y, z), parts[3]] = to_z
    return out


def record_confirmed(spot: Spot, kind: str, to_z: int, path: Path | None = None) -> bool:
    """Add a floor change the bot just took; True when it was new."""
    path = FLOOR_CHANGES_FILE if path is None else path
    with _lock:
        known = load_confirmed(path)
        if known.get((spot, kind)) == to_z:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not path.exists()
            with path.open("a", encoding="utf-8") as out:
                if new_file:
                    out.write(_HEADER)
                out.write(f"{spot[0]},{spot[1]},{spot[2]},{kind},{to_z}\n")
        except OSError as exc:
            log.warning("floor changes: cannot write %s: %s", path, exc)
            return False
    log.info("floor changes: confirmed %s at %d,%d,%d leads to floor %d", kind, *spot, to_z)
    return True
