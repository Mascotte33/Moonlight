"""Find a creature on the game screen by the name the client writes above it.

Used only by the ``boat`` waypoint, to find the captain (never while hunting).
NPCs have no health bar, so the names themselves are looked for: the client
writes the name of a creature at full health in one green, ``NAME_GREEN``
(speech is yellow, so a captain's own answers are not taken for names). The
green pixels are grouped into lines, each line is read with Tesseract (the
same engine as the server log OCR), and the creature stands on the tile
below the line.

Lines are read nearest the player first (Chebyshev distance in tiles):

* a name at least :data:`SURE` like the wanted one ends the search at once;
* otherwise every line is read and the closest name at least :data:`ACCEPT`
  alike wins;
* none that close (a name half covered by speech): no creature, the caller
  looks again.

Thais 2026-11-26: real frames read "Benjamin", "Yuzuki Ruka"; a captain
half covered by his speech read "CantaRlyehea" (not taken).

# doc: docs/prs/boat.md#find-captain
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

import cv2
import numpy as np

from moonlight.perception.creatures import GRID_COLUMNS, GRID_ROWS, PLAYER_SLOT

__all__ = ["ACCEPT", "NAME_GREEN", "SURE", "find_named", "name_likeness", "name_lines"]

log = logging.getLogger(__name__)

#: Likeness (0..1) a read name needs to count as the wanted one.
ACCEPT = 0.7
#: Likeness that ends the search at once.
SURE = 0.8
#: RGB of the name of a creature at full health (an NPC always is).
NAME_GREEN = (0, 192, 0)
#: How far a pixel may be from NAME_GREEN in each channel (the font's edge pixels).
_GREEN_SLACK = 40
#: A line of name text: at least this wide and between these heights (px).
_MIN_W, _MIN_H, _MAX_H = 12, 6, 30
#: From the bottom of a name to the top of its creature's tile (px; does not scale).
_NAME_TO_TILE = 28
#: Tesseract sees small text badly: a line is scaled up this much first.
_SCALE = 3
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


def _read(ink: np.ndarray) -> str:
    big = cv2.resize(ink, None, fx=_SCALE, fy=_SCALE, interpolation=cv2.INTER_NEAREST)
    big = cv2.copyMakeBorder(big, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
    try:
        import pytesseract

        text = pytesseract.image_to_string(big, config=_TESS_CONFIG)
    except Exception as exc:  # a missing Tesseract must not kill the bot
        log.warning("npc names: Tesseract failed: %s", exc)
        return ""
    return re.sub(r"\s+", " ", text).strip()


def name_lines(
    frame: np.ndarray, grid: tuple[int, int, int, int]
) -> list[tuple[tuple[int, int], np.ndarray]]:
    """Each line of green name text in the game grid: the slot ``(col, row)``
    of the creature under it and the line as black-on-white ink."""
    gx, gy, gw, gh = grid
    view = frame[gy : gy + gh, gx : gx + gw, :3].astype(np.int16)
    green = np.ones(view.shape[:2], bool)
    for channel, value in enumerate(NAME_GREEN):
        green &= np.abs(view[..., channel] - value) <= _GREEN_SLACK
    mask = green.astype(np.uint8)
    joined = cv2.dilate(mask, np.ones((3, 9), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(joined)
    tw, th = gw / GRID_COLUMNS, gh / GRID_ROWS
    lines = []
    for i in range(1, count):
        x, y, w, h, _ = stats[i]
        if w < _MIN_W or not _MIN_H <= h <= _MAX_H:
            continue
        col = int((x + w / 2) // tw)
        row = int((y + h + _NAME_TO_TILE + th / 2) // th)
        if not (0 <= col < GRID_COLUMNS and 0 <= row < GRID_ROWS):
            continue
        ink = np.where(mask[y : y + h, x : x + w] > 0, 0, 255).astype(np.uint8)
        lines.append(((col, row), ink))
    return lines


def find_named(
    frame: np.ndarray, grid: tuple[int, int, int, int], wanted: str
) -> tuple[tuple[int, int], str, float] | None:
    """The game-grid slot ``(col, row)`` of the creature named *wanted*, the
    name read there and its likeness; None when no name is :data:`ACCEPT` alike."""
    px, py = PLAYER_SLOT
    lines = sorted(
        name_lines(frame, grid),
        key=lambda line: (max(abs(line[0][0] - px), abs(line[0][1] - py)), line[0]),
    )
    best: tuple[tuple[int, int], str, float] | None = None
    for slot, ink in lines:
        name = _read(ink)
        likeness = name_likeness(name, wanted)
        log.debug("npc names: slot %s reads %r (%.2f like %r)", slot, name, likeness, wanted)
        if likeness >= SURE:
            return slot, name, likeness
        if likeness >= ACCEPT and (best is None or likeness > best[2]):
            best = (slot, name, likeness)
    return best
