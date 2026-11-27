"""A ``boat`` waypoint under way: find the captain by name, walk next to him,
talk, and wait to land.

The cavebot asks :meth:`BoatTrip.step` each tick what to do; the talk itself
is the cavebot's NPC talk, after which it calls :meth:`BoatTrip.sail`.

# doc: docs/prs/boat.md#boat-waypoint
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from moonlight.cavebot.route import Waypoint
from moonlight.cavebot.travel import TravelNpc, landed
from moonlight.core.state import Position
from moonlight.perception.creatures import PLAYER_SLOT
from moonlight.perception.minimap import Rect
from moonlight.perception.npc_names import find_named

__all__ = [
    "BOAT_SHOTS_DIR",
    "BOAT_TIMEOUT",
    "BoatStep",
    "BoatTrip",
    "read_names_later",
    "save_shot",
]

logger = logging.getLogger(__name__)

#: Seconds a boat waypoint looks for its captain, walks next to him, or waits to land.
BOAT_TIMEOUT = 15.0
# doc: docs/prs/boat.md#boat-waypoint
#: Seconds the search for the captain goes on near his sqm: his speech to other
#: players covers his name while he answers them (Thais 2026-11-26).
_SEARCH_TIMEOUT = 30.0
#: The search for the captain waits within this many sqm of his home sqm (he
#: wanders: at the view's edge he can be out of it, Thais 2026-11-26).
_NEAR_HOME = 2
#: Seconds between two reads of the names on screen while a boat waypoint runs.
_RELOOK = 0.5
#: Screenshots of a failed boat trip (bot.log names each).
BOAT_SHOTS_DIR = Path("logs") / "boat"

#: A name read: the creature's game-grid slot, the name read and how alike it is.
NameRead = tuple[tuple[int, int], str, float]

_name_reader: ThreadPoolExecutor | None = None


def read_names_later(frame: np.ndarray, grid: Rect, name: str) -> Future[NameRead | None]:
    """:func:`find_named` on a worker thread: Tesseract takes ~0.1 s a name."""
    global _name_reader
    if _name_reader is None:
        _name_reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="npc-names")
    return _name_reader.submit(find_named, frame, grid, name)


def save_shot(frame: np.ndarray | None) -> None:
    """Keep what the bot saw when a trip failed."""
    if frame is None:
        return
    BOAT_SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    shot = BOAT_SHOTS_DIR / time.strftime("boat_%Y%m%d_%H%M%S.png")
    picture = cv2.cvtColor(np.ascontiguousarray(frame), cv2.COLOR_RGB2BGR)
    if cv2.imwrite(str(shot), picture):
        logger.warning("cavebot: boat: screenshot %s", shot)


@dataclass(frozen=True)
class BoatStep:
    """What the cavebot does this tick for the trip.

    Exactly one is set: *walk* (walk there), *talk* (say these lines to the
    captain), *landed*, *failed* (why: stop the route); none: stand still,
    *busy* meaning it is not stuck (looking, sailing).
    """

    walk: Waypoint | None = None
    talk: tuple[str, ...] = ()
    landed: bool = False
    failed: str = ""
    busy: bool = False


class BoatTrip:
    """One ``boat`` waypoint: the captain on its sqm, *destination* its label."""

    def __init__(self, captain: TravelNpc, destination: str) -> None:
        self.captain = captain
        self.destination = destination
        #: When the captain's sqm came in view (the search timeout runs from it).
        self._since: float | None = None
        #: When the player stood near his sqm (the search timeout runs from it).
        self._near_since: float | None = None
        #: When the captain was found (the walk-next-to-him timeout runs from it).
        self._found_at: float | None = None
        self._looked = float("-inf")
        #: The name read in progress and the position it was grabbed at.
        self._scan: Future[NameRead | None] | None = None
        self._scan_pos: Position | None = None
        self._captain_tile: tuple[int, int, int] | None = None
        #: After the talk: give up waiting to land at this time.
        self._sail_until: float | None = None

    @property
    def captain_tile(self) -> tuple[int, int, int] | None:
        """The captain's sqm as last read."""
        return self._captain_tile

    @property
    def searching(self) -> bool:
        """His sqm came in view: the bot looks for him."""
        return self._since is not None

    @property
    def sailing(self) -> bool:
        """The talk is done: waiting to land."""
        return self._sail_until is not None

    def sail(self, now: float) -> None:
        """The captain was asked: wait to land."""
        self._sail_until = now + BOAT_TIMEOUT
        logger.info("cavebot: boat: asked %s for %r", self.captain.name, self.destination)

    def step(
        self,
        wp: Waypoint,
        pos: Position,
        now: float,
        *,
        settled: bool,
        screen: Callable[[], np.ndarray | None],
        region: Rect,
        free: Callable[[int, int, int], bool],
    ) -> BoatStep:
        """*settled*: the view stopped scrolling (names read mid-step are a
        tile off); *free*: a sqm the player can stand on."""
        if self._sail_until is not None:
            if landed(self.destination, pos.x, pos.y, pos.z):
                return BoatStep(landed=True, busy=True)
            if now >= self._sail_until:
                why = f"talked to {self.captain.name} but did not land in {self.destination!r}"
                return BoatStep(failed=why, busy=True)
            return BoatStep(busy=True)
        dx, dy = wp.x - pos.x, wp.y - pos.y
        if pos.z != wp.z or abs(dx) > PLAYER_SLOT[0] or abs(dy) > PLAYER_SLOT[1]:
            return BoatStep(walk=wp)  # walk until the captain's sqm is in view
        if self._since is None:
            self._since = now
            logger.info("cavebot: boat: looking for %s", self.captain.name)
        self._read(pos, now, settled, screen, region)
        if self._captain_tile is None:
            near = max(abs(dx), abs(dy)) <= _NEAR_HOME
            if not near and now - self._since < _SEARCH_TIMEOUT:
                return BoatStep(walk=wp)  # his sqm at the view's edge: he may stand off it
            if self._near_since is None:
                self._near_since = now
            if now - self._near_since >= _SEARCH_TIMEOUT:
                return BoatStep(failed=f"{self.captain.name} not found on screen", busy=True)
            return BoatStep(busy=True)  # looking around is not being stuck
        cx, cy, cz = self._captain_tile
        if cz == pos.z and max(abs(cx - pos.x), abs(cy - pos.y)) <= 1:
            logger.info("cavebot: boat: next to %s at %s,%s, talking", self.captain.name, cx, cy)
            return BoatStep(talk=("hi", self.destination, "yes"))
        assert self._found_at is not None
        if now - self._found_at >= BOAT_TIMEOUT:
            return BoatStep(failed=f"could not get next to {self.captain.name}")
        stand = self._stand(pos, free)
        if stand is None:
            return BoatStep()  # every sqm around him taken: look again
        return BoatStep(walk=Waypoint(*stand, "boat", self.destination))

    def _read(
        self,
        pos: Position,
        now: float,
        settled: bool,
        screen: Callable[[], np.ndarray | None],
        region: Rect,
    ) -> None:
        """Take a finished name read, and start the next one every ``_RELOOK`` s."""
        if self._scan is not None and self._scan.done():
            scan, base = self._scan, self._scan_pos
            self._scan = None
            try:
                found = scan.result()
            except Exception as exc:  # the reader must not kill the tick
                logger.warning("cavebot: boat: reading names failed: %s", exc)
                found = None
            if found is not None and base is not None:
                (col, row), name, likeness = found
                tile = (base.x + col - PLAYER_SLOT[0], base.y + row - PLAYER_SLOT[1], base.z)
                if tile != self._captain_tile:
                    logger.info(
                        "cavebot: boat: %s at %s,%s (read %r, %.0f%% alike)",
                        self.captain.name, tile[0], tile[1], name, likeness * 100,
                    )  # fmt: skip
                if self._found_at is None:
                    self._found_at = now
                self._captain_tile = tile
        if self._scan is None and settled and now - self._looked >= _RELOOK:
            frame = screen()
            if frame is not None:
                self._looked, self._scan_pos = now, pos
                self._scan = read_names_later(frame.copy(), region, self.captain.name)

    def _stand(
        self, pos: Position, free: Callable[[int, int, int], bool]
    ) -> tuple[int, int, int] | None:
        """The free sqm next to the captain nearest to the player."""
        assert self._captain_tile is not None
        cx, cy, cz = self._captain_tile
        spots = [
            (cx + dx, cy + dy, cz)
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            if (dx, dy) != (0, 0) and free(cx + dx, cy + dy, cz)
        ]
        if not spots:
            return None
        return min(spots, key=lambda s: (max(abs(s[0] - pos.x), abs(s[1] - pos.y)), s))
