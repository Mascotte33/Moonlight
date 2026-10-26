"""Creatures on the game screen, found by their health bars.

Every creature on screen (monster, NPC, player) has a health bar above it:
a 31x4 px rectangle with a 1 px black border and a 2 px tall fill. The fill
is one color, with black on the right for the missing HP. The bar does not
scale with the game view, so its size is the same at every tile size.

:func:`find_creatures` returns the game-grid slot ``(col, row)`` under each
bar. The bar's center sits over its creature's tile center, just above the
tile, so the slot is the tile half a tile below the bar. The player's own
slot (7, 5) is left out.

:func:`predict` guesses where each creature steps next: the square one
further in the direction it last moved.

# doc: docs/prs/keys-walking.md#creature-scan
"""

from __future__ import annotations

import cv2
import numpy as np

__all__ = ["BAR_W", "PLAYER_SLOT", "find_creatures", "predict"]

BAR_W = 31
_BAR_H = 4
_BLACK = 25
_FILL = 80
_COLS, _ROWS = 15, 11
#: The player's own game-grid slot (col, row).
PLAYER_SLOT = (7, 5)
_RUN_KERNEL = np.ones((1, BAR_W), np.uint8)


def _runs(mask: np.ndarray) -> np.ndarray:
    """True at ``[y, x]`` when ``mask[y, x:x+BAR_W]`` is all True."""
    # An erosion with a 1xBAR_W kernel anchored left is "all True from x on".
    eroded = cv2.erode(mask.view(np.uint8), _RUN_KERNEL, anchor=(0, 0), borderValue=0)
    out: np.ndarray = eroded[:, : mask.shape[1] - BAR_W + 1].view(bool)
    return out


def find_creatures(frame: np.ndarray, grid: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    """Slots ``(col, row)`` of the creatures in the game grid, sorted.

    *frame* is a BGR screenshot, *grid* the calibrated ``(x, y, w, h)``.
    """
    gx, gy, gw, gh = grid
    crop = frame[gy : gy + gh, gx : gx + gw]
    if crop.shape[0] < _BAR_H or crop.shape[1] < BAR_W:
        return []
    # cv2.max is ~10x faster than crop.max(axis=2) on a full game grid.
    bright = cv2.max(cv2.max(crop[:, :, 0], crop[:, :, 1]), crop[:, :, 2])
    black = bright < _BLACK
    run = _runs(black)
    sides = black[1:-2] & black[2:-1]
    n = run.shape[1]
    ys, xs = np.nonzero(
        run[:-3]
        & run[3:]
        & sides[:, :n]
        & sides[:, BAR_W - 1 :]
        # A creature at 0 HP has no bar, so the first fill pixel has color.
        & (bright[1:-2, 1 : n + 1] >= _FILL)
    )
    tw, th = gw / _COLS, gh / _ROWS
    slots = set()
    for y, x in zip(ys.tolist(), xs.tolist(), strict=True):
        col = int((x + BAR_W / 2) // tw)
        row = int((y + th / 2) // th)
        if 0 <= col < _COLS and 0 <= row < _ROWS:
            slots.add((col, row))
    slots.discard(PLAYER_SLOT)
    return sorted(slots)


def predict(now: list[tuple[int, int]], before: list[tuple[int, int]]) -> set[tuple[int, int]]:
    """The next square of each creature in *now* that moved one square.

    Each creature is paired with the nearest one in *before* (the previous
    scan, same coordinates). A creature that moved one square is assumed
    to keep going the same way.
    """
    out = set()
    for x, y in now:
        near = min(before, key=lambda b: max(abs(b[0] - x), abs(b[1] - y)), default=None)
        if near is not None and max(abs(near[0] - x), abs(near[1] - y)) == 1:
            out.add((2 * x - near[0], 2 * y - near[1]))
    return out
