"""Drop unwanted items: drag each picked item out of the open backpacks.

A free account has no loot sorting, so the owner picks the items to throw
away (Cavebot tab, Settings: "+" cuts each item's picture). Every 1-3 min
(random), once no monster is in the battle list and no fight or town action
is on, a round starts. Each round looks right of the game window for every
picture. Each found item is dragged (a whole stack moves in one drag) to a
random free walkable sqm within 2 of the character. If the item is still in
the same place 0.6 s later, another free sqm is tried, 3 tries in all. With
no free sqm the round ends and the next timer tries again.

# doc: docs/prs/drop-items.md#drop-items
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from moonlight.cavebot.town import find_picture
from moonlight.core.state import WorldState
from moonlight.hardware.input import ClickSide, InputBackend
from moonlight.perception.corpses import slot_ring
from moonlight.perception.creatures import PLAYER_SLOT

__all__ = [
    "DROPPING_FILE", "DROP_ITEMS_DIR", "DropItems", "drop_picture_path", "drop_pictures",
    "dropping", "free_drop_slots", "saved_pictures", "set_dropping",
]

#: The picked item pictures, ``<name>.png``, in the profiles folder.
DROP_ITEMS_DIR = "drop_items"
#: A round starts this many seconds (random in between) after the last one.
ROUND_EVERY_SECONDS = (60.0, 180.0)
#: Items land at most this many sqm from the character.
DROP_REACH = 2
#: Sqm tried for one item before it is left for this round.
TRIES_PER_ITEM = 3
#: Seconds after a drag before the backpacks are looked at again.
LOOK_AGAIN_SECONDS = 0.6
#: An item found within this many pixels of where it was dragged from did not move.
SAME_SPOT_PIXELS = 3

log = logging.getLogger(__name__)

Point = tuple[int, int]


#: The names on the drop list, one per line, next to the saved pictures.
DROPPING_FILE = "dropping.txt"


def saved_pictures(folder: Path) -> list[Path]:
    """Every item picture clipped into *folder*, by name (dropped or not)."""
    return sorted(folder.glob("*.png"))


def dropping(folder: Path) -> list[str]:
    """The names on the drop list; with no list yet, every saved picture."""
    path = folder / DROPPING_FILE
    if not path.exists():
        return [p.stem for p in saved_pictures(folder)]
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def set_dropping(folder: Path, names: list[str]) -> None:
    """Save the drop list (unique names, in order)."""
    folder.mkdir(parents=True, exist_ok=True)
    unique = list(dict.fromkeys(names))
    (folder / DROPPING_FILE).write_text("".join(f"{n}\n" for n in unique), encoding="utf-8")


def drop_pictures(folder: Path) -> list[Path]:
    """The pictures of the items on the drop list, by name."""
    names = set(dropping(folder))
    return [p for p in saved_pictures(folder) if p.stem in names]


def drop_picture_path(folder: Path, name: str) -> Path:
    """Where the picture of item *name* is saved ("+" on the Cavebot tab).

    Raises:
        ValueError: On an empty name or one a file name cannot hold.
    """
    name = name.strip()
    if not name or any(c in name for c in '\\/:*?"<>|') or name.startswith("."):
        raise ValueError(f"not an item name: {name!r}")
    return folder / f"{name}.png"


def free_drop_slots(
    snap: WorldState, walkable: Callable[[int, int, int], bool] | None
) -> list[Point]:
    """Screen slots within ``DROP_REACH`` of the character an item can land on.

    Not the character's own sqm, not one a creature stands on, and (with the
    walk map and a known position) not a wall or a floor change.
    """
    pc, pr = PLAYER_SLOT
    pos = snap.position
    taken = set(snap.creature_slots)
    slots = []
    for dx in range(-DROP_REACH, DROP_REACH + 1):
        for dy in range(-DROP_REACH, DROP_REACH + 1):
            slot = (pc + dx, pr + dy)
            if (dx, dy) == (0, 0) or slot in taken:
                continue
            if walkable is not None and pos.x and not walkable(pos.x + dx, pos.y + dy, pos.z):
                continue
            slots.append(slot)
    return slots


class DropItems:
    """Drops the picked items, one wire command per :meth:`step`."""

    def __init__(
        self,
        input: InputBackend,
        to_wire: Callable[[Point], Point],
        grab: Callable[[], np.ndarray],
        game_region: tuple[int, int, int, int],
        pictures_dir: Path,
        walkable: Callable[[int, int, int], bool] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self._input = input
        self._to_wire = to_wire
        self._grab = grab
        self._game_region = game_region
        self.pictures_dir = pictures_dir
        self._walkable = walkable
        self._clock = clock
        self._rng = rng or random.Random()
        self._next_round_at = clock() + self._rng.uniform(*ROUND_EVERY_SECONDS)
        self.in_round = False
        #: The drag's wire commands still to send.
        self._sends: list[Callable[[], None]] = []
        self._look_again_at = float("-inf")
        #: The item dragged last and where it was, checked at the next look.
        self._dragged: tuple[Path, Point] | None = None
        #: Slots tried for ``_dragged``.
        self._tried: list[Point] = []
        #: Items left alone this round (no sqm took them).
        self._left: list[tuple[Path, Point]] = []

    def due(self, snap: WorldState) -> bool:
        """True while a round runs, or when its timer is up and no monster is in view."""
        return self.in_round or (not snap.monsters and self._clock() >= self._next_round_at)

    def step(self, snap: WorldState) -> None:
        """Send one command of the drag, or look at the backpacks and start the next."""
        now = self._clock()
        if not self.in_round:
            self.in_round, self._left, self._dragged, self._tried = True, [], None, []
        if self._sends:
            dropped = getattr(self._input, "dropped", 0)
            self._sends[0]()
            if getattr(self._input, "dropped", 0) == dropped:  # the rate limit kept it
                self._sends.pop(0)
            if not self._sends:
                self._look_again_at = now + LOOK_AGAIN_SECONDS
            return
        if now < self._look_again_at:
            return
        if snap.monsters:
            self._end_round(now, wait=False)  # start again once the screen is clear
            return
        found = self._find()
        if found is not None and self._dragged is not None and self._same(found, self._dragged):
            if len(self._tried) >= TRIES_PER_ITEM:
                log.warning("drop: %s stayed after %d tries, left", found[0].stem, len(self._tried))
                self._left.append(found)
                found = self._find()
        if found is None:
            self._end_round(now)
            return
        if self._dragged is None or not self._same(found, self._dragged):
            self._tried = []  # a new item (the last one moved)
        slots = [s for s in free_drop_slots(snap, self._walkable) if s not in self._tried]
        if not slots:
            log.info("drop: no free sqm within %d, next round", DROP_REACH)
            self._end_round(now)
            return
        slot = self._rng.choice(slots)
        self._tried.append(slot)
        self._dragged = found
        path, item = found
        a, b = self._to_wire(item), self._to_wire(slot_ring(self._game_region, slot).point)
        send = self._input
        self._sends = [
            lambda: send.mouse_to(*a),
            lambda: send.mouse_down(ClickSide.LEFT),
            lambda: send.mouse_to(*b),
            lambda: send.mouse_up(ClickSide.LEFT),
        ]
        dx, dy = slot[0] - PLAYER_SLOT[0], slot[1] - PLAYER_SLOT[1]
        log.info("drop: %s -> (%d, %d)", path.stem, snap.position.x + dx, snap.position.y + dy)

    def _end_round(self, now: float, wait: bool = True) -> None:
        self.in_round = False
        self._next_round_at = now + (self._rng.uniform(*ROUND_EVERY_SECONDS) if wait else 0.0)

    def _find(self) -> tuple[Path, Point] | None:
        """The first picked item seen right of the game window, not left this round.

        # ponytail: the best match per picture only; a second stack of a left
        # item waits for the next round.
        """
        frame = self._grab()
        right = self._game_region[0] + self._game_region[2]
        side = np.ascontiguousarray(frame[:, right:])
        for path in drop_pictures(self.pictures_dir):
            at = find_picture(side, path)
            if at is None:
                continue
            found = (path, (at[0] + right, at[1]))
            if not any(self._same(found, left) for left in self._left):
                return found
        return None

    @staticmethod
    def _same(a: tuple[Path, Point], b: tuple[Path, Point]) -> bool:
        (path_a, (xa, ya)), (path_b, (xb, yb)) = a, b
        return path_a == path_b and max(abs(xa - xb), abs(ya - yb)) <= SAME_SPOT_PIXELS
