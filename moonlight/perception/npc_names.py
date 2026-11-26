"""Find a creature on the game screen by the name drawn above its health bar.

Used only by the ``boat`` waypoint, to find the captain (never while hunting).
The client writes each creature's name just above its health bar, in a
coloured fill with a dark outline. Each name is cut out, made black on white
and read with Tesseract (the same engine as the server log OCR).

The search starts at the player and goes outward ring by ring (Chebyshev
distance in tiles), so a captain next to the player is read first and the
rest of the screen is read only when needed:

* a name at least :data:`SURE` like the wanted one ends the search at once;
* otherwise every bar is read and the closest name at least :data:`ACCEPT`
  alike wins;
* none that close: no creature (the caller looks again).

# doc: docs/prs/boat.md#find-captain
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

import cv2
import numpy as np

from moonlight.perception.creatures import BAR_W, PLAYER_SLOT, bar_spots

__all__ = ["ACCEPT", "SURE", "find_named", "name_likeness", "read_name"]

log = logging.getLogger(__name__)

#: Likeness (0..1) a read name needs to count as the wanted one.
ACCEPT = 0.7
#: Likeness that ends the search at once (no farther bar can beat it by much).
SURE = 0.8
#: The name box above a bar: half its width each side of the bar's centre, its height.
_NAME_HALF_W, _NAME_H = 90, 16
#: A name pixel is at least this bright in its brightest channel (the outline is dark).
_TEXT_MIN = 110
#: Tesseract sees small text badly: the box is scaled up this much first.
_SCALE = 4
#: Letters only: pytesseract splits the config like a shell, so no quote or space in it.
_TESS_CONFIG = (
    "--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)


def name_likeness(read: str, wanted: str) -> float:
    """How alike two names are, 0..1 (case and spaces ignored: Tesseract drops
    the space of a short gap)."""
    a = "".join(read.lower().split())
    b = "".join(wanted.lower().split())
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _name_box(frame: np.ndarray, bar: tuple[int, int]) -> np.ndarray | None:
    x, y = bar
    cx = x + BAR_W // 2
    x0, x1 = max(0, cx - _NAME_HALF_W), min(frame.shape[1], cx + _NAME_HALF_W)
    y0, y1 = max(0, y - _NAME_H), max(0, y - 1)
    if x1 - x0 < BAR_W or y1 - y0 < _NAME_H // 2:
        return None
    return frame[y0:y1, x0:x1]


def _ink(box: np.ndarray) -> np.ndarray:
    """Black text on white, scaled up for Tesseract."""
    bright = box if box.ndim == 2 else cv2.max(cv2.max(box[..., 0], box[..., 1]), box[..., 2])
    ink = np.where(bright >= _TEXT_MIN, 0, 255).astype(np.uint8)
    big = cv2.resize(ink, None, fx=_SCALE, fy=_SCALE, interpolation=cv2.INTER_NEAREST)
    return cv2.copyMakeBorder(big, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)


def read_name(frame: np.ndarray, bar: tuple[int, int]) -> str:
    """The name above the bar whose top-left is frame pixel *bar*; ``""`` if unreadable."""
    box = _name_box(frame, bar)
    if box is None:
        return ""
    ink = _ink(box)
    if (ink == 0).sum() < 10:
        return ""  # nothing drawn there
    try:
        import pytesseract

        text = pytesseract.image_to_string(ink, config=_TESS_CONFIG)
    except Exception as exc:  # a missing Tesseract must not kill the bot
        log.warning("npc names: Tesseract failed: %s", exc)
        return ""
    return re.sub(r"\s+", " ", text).strip()


def find_named(
    frame: np.ndarray, grid: tuple[int, int, int, int], wanted: str
) -> tuple[tuple[int, int], str, float] | None:
    """The game-grid slot ``(col, row)`` of the creature named *wanted*, the
    name read there and its likeness; None when no name is :data:`ACCEPT` alike."""
    spots = bar_spots(frame, grid)
    px, py = PLAYER_SLOT
    order = sorted(spots, key=lambda s: (max(abs(s[0] - px), abs(s[1] - py)), s))
    best: tuple[tuple[int, int], str, float] | None = None
    for slot in order:
        name = read_name(frame, spots[slot])
        likeness = name_likeness(name, wanted)
        log.debug("npc names: slot %s reads %r (%.2f like %r)", slot, name, likeness, wanted)
        if likeness >= SURE:
            return slot, name, likeness
        if likeness >= ACCEPT and (best is None or likeness > best[2]):
            best = (slot, name, likeness)
    return best
