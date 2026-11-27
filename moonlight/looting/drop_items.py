"""Drop unwanted items: drag each picked item out of the open backpacks.

A free account has no loot sorting, so the owner picks the items to throw
away (Cavebot tab, Settings: "+" cuts each item's picture). Every 1-3 min
(random), once the mouse is free and no town action is on, a round starts:
also with monsters in view and between a fight's clicks (owner 2026-11-27:
the mouse sat idle while the items waited for an empty battle list).
Each round looks right of the game window for every
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
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from moonlight.core.hand import Hand, Send
from moonlight.core.state import WorldState
from moonlight.hardware.input import ClickSide, InputBackend
from moonlight.perception.corpses import slot_ring
from moonlight.perception.creatures import PLAYER_SLOT

__all__ = [
    "DROPPING_FILE",
    "DROP_ITEMS_DIR",
    "DropItems",
    "drop_picture_path",
    "drop_pictures",
    "dropping",
    "free_drop_slots",
    "saved_pictures",
    "set_dropping",
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


#: Largest squared colour difference per pixel and channel of the item's body that is
#: still the item. Live 2026-11-21: the clipped items 0..6, the closest other item
#: 282. The old grey 0.8 match threw away an item that was not on the list.
DROP_MATCH_MAX = 50.0
#: The stack count's corner, left out of the match: rows from this share down ...
_COUNT_TOP = 0.6
#: ... and columns from this share right.
_COUNT_LEFT = 0.35
#: An item pixel: channels this far apart (the slot is gray) ...
_ITEM_SATURATION = 12
#: ... or this bright (the slot is darker).
_ITEM_BRIGHT = 150
#: Fewer item pixels than this: the whole picture is matched.
_ITEM_MIN_PIXELS = 30


#: Per picture file: (mtime, mask, mask * picture, sum of mask * picture², kept count).
_PICTURES: dict[Path, tuple[float, np.ndarray, np.ndarray, float, int]] = {}


def _picture(path: Path) -> tuple[np.ndarray, np.ndarray, float, int] | None:
    """The item at *path* as a float mask, the masked picture, Σ mask·pic² and the
    masked value count, cached until the file changes."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cached = _PICTURES.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1:]
    pic = cv2.imread(str(path))  # the file holds the frame's RGB as is
    if pic is None:
        return None
    h, w = pic.shape[:2]
    # Only the item's own pixels: the slot behind it is lighter or darker by
    # backpack and client (2026-11-22: a torch clipped on a lighter slot never
    # matched). The slot is plain gray; items are coloured or bright.
    px = pic.astype(np.int32)
    keep = (px.max(axis=2) - px.min(axis=2) > _ITEM_SATURATION) | (px.max(axis=2) >= _ITEM_BRIGHT)
    keep[int(h * _COUNT_TOP) :, int(w * _COUNT_LEFT) :] = False
    if keep.sum() < _ITEM_MIN_PIXELS:  # a plain gray item: the whole picture
        keep[:] = True
        keep[int(h * _COUNT_TOP) :, int(w * _COUNT_LEFT) :] = False
    mask = np.repeat(keep[:, :, None], 3, axis=2).astype(np.float32)
    masked = mask * pic.astype(np.float32)
    entry = (mtime, mask, masked, float((masked * pic).sum()), int(mask.sum()))
    _PICTURES[path] = entry
    return entry[1:]


def _scores(frame: np.ndarray, path: Path) -> tuple[np.ndarray, int, int] | None:
    """Per-pixel match of the item at *path* in *frame* (mean squared difference), w, h.

    Σ m(I-T)² = Σ m·I² - 2 Σ (m·T)·I + Σ m·T²: two plain correlations, far
    faster than OpenCV's masked match (a look over 44 items froze the bot for
    up to 2.7 s, 2026-11-26).
    """
    picture = _picture(path)
    if picture is None:
        return None
    mask, masked, sum_t2, count = picture
    h, w = mask.shape[:2]
    if h > frame.shape[0] or w > frame.shape[1]:
        return None
    image = frame.astype(np.float32)
    i2 = cv2.matchTemplate(image * image, mask, cv2.TM_CCORR)
    it = cv2.matchTemplate(image, masked, cv2.TM_CCORR)
    scores = np.maximum(i2 - 2.0 * it + sum_t2, 0.0)
    return scores / float(count), w, h


def find_item(frame: np.ndarray, path: Path) -> Point | None:
    """Centre of the item pictured at *path* in *frame* (RGB), or None.

    Strict on purpose: a wrong match throws an item away. Compared in colour,
    the stack count's corner left out (it changes as the stack does).
    """
    found = _scores(frame, path)
    if found is None:
        return None
    scores, w, h = found
    _, _, (x, y), _ = cv2.minMaxLoc(scores)
    if scores[y, x] > DROP_MATCH_MAX:
        return None
    return x + w // 2, y + h // 2


def count_items(frame: np.ndarray, path: Path) -> int:
    """How many slots in *frame* hold the item at *path*."""
    found = _scores(frame, path)
    if found is None:
        return 0
    hits = (found[0] <= DROP_MATCH_MAX).astype(np.uint8)
    return int(cv2.connectedComponents(hits)[0]) - 1


def free_drop_slots(
    snap: WorldState, walkable: Callable[[int, int, int], bool] | None
) -> list[Point]:
    """Screen slots within ``DROP_REACH`` of the character an item can land on.

    The character's own sqm too (items dropped while walking), not one a
    creature stands on, and (with the walk map and a known position) not a wall or a floor change.
    """
    pc, pr = PLAYER_SLOT
    pos = snap.position
    taken = set(snap.creature_slots)
    slots = []
    for dx in range(-DROP_REACH, DROP_REACH + 1):
        for dy in range(-DROP_REACH, DROP_REACH + 1):
            slot = (pc + dx, pr + dy)
            if slot in taken and (dx, dy) != (0, 0):
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
        background: bool = False,
    ) -> None:
        #: *background*: the backpack look runs on a worker thread (a look over
        #: every picture takes about a second; the loop walks on meanwhile).
        self._pool = ThreadPoolExecutor(1, "drop-look") if background else None
        self._look: Future[tuple[Path, Point] | None] | None = None
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
        self._dragged_count = 0
        self._side: np.ndarray | None = None
        #: Slots tried for ``_dragged``.
        self._tried: list[Point] = []
        #: Items left alone this round (no sqm took them).
        self._left: list[tuple[Path, Point]] = []
        #: The loop's hand (who holds the mouse); None: always free.
        self.hand: Hand | None = None

    @property
    def mid_drag(self) -> bool:
        """True while a drag's commands are still to send: it must not be cut off."""
        return bool(self._sends)

    def due(self, snap: WorldState) -> bool:
        """True while a round runs, or when its timer is up."""
        return self.in_round or self._clock() >= self._next_round_at

    # doc: docs/prs/drop-items.md#drop-items
    def wants(self, snap: WorldState) -> bool:
        """The loop's claim: a round is due and may run now.

        A round starts only while the mouse is free (no click in flight and
        no cavebot drag, typing or NPC talk) and no corpse ring waits: items
        dropped next to the player covered the corpse's ring and it was never
        looted (2026-11-21). Never next to a rope spot or stairs, unless a
        drag is half sent.
        """
        if not self.due(snap):
            return False
        hand = self.hand
        if hand is None:
            return True
        if not self.in_round and (hand.loot_pending(snap) or hand.busy):
            return False
        return self.mid_drag or not hand.near_floor_change

    def act(self, snap: WorldState, send: Send) -> None:
        """One round step. Plain walking goes on while the items drop (owner
        2026-11-26); a waypoint that needs the mouse or a stop waits for the
        round's end."""
        hand = self.hand
        walking = hand is not None and hand.walking_on
        if hand is not None and not walking:
            hand.release_walk_key()
        send(lambda: self.step(snap))
        if hand is None:
            return
        if walking:
            hand.walk(snap)
        elif not self.in_round:
            hand.resume()  # drop time is not "stuck"

    def step(self, snap: WorldState) -> None:
        """Send one command of the drag, or look at the backpacks and start the next."""
        now = self._clock()
        if not self.in_round:
            self.in_round, self._left, self._dragged, self._tried = True, [], None, []
            self._dragged_count = 0
        if self._sends and self._sends[0] == self._check:
            self._sends.pop(0)
            if not self._still_there():
                path, item = self._dragged if self._dragged else (Path("?"), (0, 0))
                log.warning("drop: %s moved before the drag, not dragged; looking again", path.stem)
                self._sends, self._dragged, self._tried = [], None, []
                self._look_again_at = now + LOOK_AGAIN_SECONDS
            return
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
        if self._pool is not None:
            if self._look is None:
                self._look = self._pool.submit(self._find_in, self._grab())
            if not self._look.done():
                return  # the look runs on: walking goes on
            found = self._look.result()
            self._look = None
        else:
            found = self._find()
        if (
            found is not None
            and self._dragged is not None
            and self._same(found, self._dragged)
            and self._count(found[0]) < self._dragged_count
        ):
            # One fewer: the drop worked, the next one slid into its slot
            # (2026-11-22: a bag of torches left after 3 "tries").
            self._dragged = None
        if found is not None and self._dragged is not None and self._same(found, self._dragged):
            if len(self._tried) >= TRIES_PER_ITEM:
                log.warning("drop: %s stayed after %d tries, left", found[0].stem, len(self._tried))
                self._left.append(found)
                found = self._find() if self._pool is None else None
                if found is None and self._pool is not None:
                    return  # the next tick looks again, without the left item
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
        self._dragged_count = self._count(found[0])
        path, item = found
        a, b = self._to_wire(item), self._to_wire(slot_ring(self._game_region, slot).point)
        send = self._input
        self._sends = [
            lambda: send.mouse_to(*a),
            self._check,  # the backpack may have changed since the look: same item still there?
            lambda: send.mouse_down(ClickSide.LEFT),
            lambda: send.mouse_to(*b),
            lambda: send.mouse_up(ClickSide.LEFT),
        ]
        dx, dy = slot[0] - PLAYER_SLOT[0], slot[1] - PLAYER_SLOT[1]
        log.info("drop: %s -> (%d, %d)", path.stem, snap.position.x + dx, snap.position.y + dy)

    def _check(self) -> None:
        """A marker in the drag: :meth:`step` checks the item before the button goes down."""

    def _still_there(self) -> bool:
        """The dragged item is still at its spot in a fresh grab (a loot press or a used
        item moves the backpack's items; the wrong one would be thrown away)."""
        if self._dragged is None:
            return False
        path, (x, y) = self._dragged
        frame = self._grab()
        pic = cv2.imread(str(path))
        if pic is None:
            return False
        h, w = pic.shape[:2]
        pad = SAME_SPOT_PIXELS
        top, left = max(0, y - h // 2 - pad), max(0, x - w // 2 - pad)
        spot = find_item(np.ascontiguousarray(frame[top : y + h, left : x + w]), path)
        return spot is not None and abs(left + spot[0] - x) <= pad and abs(top + spot[1] - y) <= pad

    def start_now(self) -> None:
        """Start a round as soon as no monster is in the battle list (``bot.drop_items``)."""
        self._next_round_at = self._clock()

    def _end_round(self, now: float, wait: bool = True) -> None:
        self.in_round = False
        self._next_round_at = now + (self._rng.uniform(*ROUND_EVERY_SECONDS) if wait else 0.0)

    def _find(self) -> tuple[Path, Point] | None:
        """The first picked item seen right of the game window, not left this round.

        # ponytail: the best match per picture only; a second stack of a left
        # item waits for the next round.
        """
        return self._find_in(self._grab())

    def _find_in(self, frame: np.ndarray) -> tuple[Path, Point] | None:
        """:meth:`_find` on *frame* (also run on the worker thread)."""
        right = self._game_region[0] + self._game_region[2]
        side = self._side = np.ascontiguousarray(frame[:, right:])
        for path in drop_pictures(self.pictures_dir):
            at = find_item(side, path)
            if at is None:
                continue
            found = (path, (at[0] + right, at[1]))
            if not any(self._same(found, left) for left in self._left):
                return found
        return None

    def _count(self, path: Path) -> int:
        """Slots holding the item at *path* in the last :meth:`_find` look."""
        return 0 if self._side is None else count_items(self._side, path)

    @staticmethod
    def _same(a: tuple[Path, Point], b: tuple[Path, Point]) -> bool:
        (path_a, (xa, ya)), (path_b, (xb, yb)) = a, b
        return path_a == path_b and max(abs(xa - xb), abs(ya - yb)) <= SAME_SPOT_PIXELS
