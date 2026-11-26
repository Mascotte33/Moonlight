"""Travel NPCs: who sails from where, read from ``data/travel_npcs.csv``.

Each line is ``kind,place,npc,x,y,z``: the NPC's home sqm (it wanders around
it). A ``boat`` waypoint stands on a captain's home sqm; its label is the
destination word said to him (``venore``). The trip has landed once the
player is within :data:`LANDED_SQM` of a travel NPC of that destination.

# doc: docs/prs/boat.md#travel-npcs
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from functools import cache
from pathlib import Path

__all__ = [
    "LANDED_SQM",
    "TRAVEL_NPCS_FILE",
    "TravelNpc",
    "captain_at",
    "landed",
    "load_travel_npcs",
]

log = logging.getLogger(__name__)

TRAVEL_NPCS_FILE = Path("data") / "travel_npcs.csv"
#: A trip has landed within this many sqm of a destination travel NPC (same floor).
LANDED_SQM = 10


@dataclass(frozen=True)
class TravelNpc:
    kind: str  # ship, steamship, ferry, carpet
    place: str  # "Venore", "Carlin (West / Reokon's Tundra)"
    name: str
    x: int
    y: int
    z: int


@cache
def load_travel_npcs(path: Path = TRAVEL_NPCS_FILE) -> tuple[TravelNpc, ...]:
    """The travel NPCs of *path*; empty (logged) when it is missing or broken."""
    try:
        lines = [
            ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")
        ]
        rows = list(csv.DictReader(lines))
        return tuple(
            TravelNpc(r["kind"], r["place"], r["npc"], int(r["x"]), int(r["y"]), int(r["z"]))
            for r in rows
        )
    except (OSError, KeyError, ValueError) as exc:
        log.warning("travel: cannot read %s: %s", path, exc)
        return ()


def captain_at(
    x: int, y: int, z: int, npcs: tuple[TravelNpc, ...] | None = None
) -> TravelNpc | None:
    """The travel NPC whose home sqm is (x, y, z), or None."""
    for npc in load_travel_npcs() if npcs is None else npcs:
        if (npc.x, npc.y, npc.z) == (x, y, z):
            return npc
    return None


def _city(place: str) -> str:
    """``"Liberty Bay (East Dock)"`` -> ``"libertybay"``: the word said to a captain."""
    return "".join(place.partition("(")[0].lower().split()).replace("'", "")


def landed(
    destination: str, x: int, y: int, z: int, npcs: tuple[TravelNpc, ...] | None = None
) -> bool:
    """True when (x, y, z) is within :data:`LANDED_SQM` of a travel NPC of *destination*."""
    want = "".join(destination.lower().split()).replace("'", "")
    return any(
        _city(npc.place) == want
        and npc.z == z
        and max(abs(npc.x - x), abs(npc.y - y)) <= LANDED_SQM
        for npc in (load_travel_npcs() if npcs is None else npcs)
    )
