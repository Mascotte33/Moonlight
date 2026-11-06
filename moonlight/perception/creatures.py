"""Creatures on the game screen, found by their health bars.

Every creature on screen (monster, NPC, player) has a health bar above it:
a 31x4 px rectangle with a 1 px black border and a 2 px tall fill. The fill
is one color, with black on the right for the missing HP. The bar does not
scale with the game view, so its size is the same at every tile size.

:func:`find_creatures` returns the game-grid slot ``(col, row)`` under each
bar. The bar's center sits over its creature's tile center, just above the
tile, so the slot is the tile half a tile below the bar. The player's own
slot (7, 5) is left out. :func:`creature_bars` also gives each one's HP from
its bar's fill (docs/prs/near-targeting.md#pick-on-screen).

# doc: docs/prs/keys-walking.md#creature-scan
"""

from __future__ import annotations

import cv2
import numpy as np

__all__ = ["BAR_W", "PLAYER_SLOT", "CreatureTracker", "creature_bars", "find_creatures"]

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
    return sorted(creature_bars(frame, grid))


def creature_bars(
    frame: np.ndarray, grid: tuple[int, int, int, int]
) -> dict[tuple[int, int], float]:
    """HP percent of each creature slot ``(col, row)``, read off its bar's fill."""
    return _slots(_bar_tops(frame, grid), grid, [])


class CreatureTracker:
    """:func:`creature_bars` that puts a walking creature on the tile it walks into.

    The client moves a creature to its next tile when the step starts, but
    draws it sliding over from the old one. The tile under the bar is the
    old tile for the first half of the step, so a click there misses
    (docs/prs/near-targeting.md#walking-creatures). The tracker compares each
    bar with the last frame's and, for a bar that moved, takes the tile ahead
    in the direction it moves. While the player walks the whole view slides,
    so no bar is moved ahead then.
    """

    def __init__(self) -> None:
        self._last_tops: list[tuple[int, int, float]] = []
        self._last_view: np.ndarray | None = None

    def read(
        self, frame: np.ndarray, grid: tuple[int, int, int, int]
    ) -> dict[tuple[int, int], float]:
        """HP percent of each creature slot ``(col, row)``."""
        tops = _bar_tops(frame, grid)
        view = _view_sample(frame, grid)
        last_tops, last_view = self._last_tops, self._last_view
        self._last_tops, self._last_view = tops, view
        if last_view is None or last_view.shape != view.shape or not _view_still(last_view, view):
            last_tops = []
        return _slots(tops, grid, last_tops)


#: Pixels a bar must move between frames to count as walking.
_WALK_PX = 2
#: The camera check samples every _VIEW_STEP-th pixel of the game view.
_VIEW_STEP = 4


def _view_sample(frame: np.ndarray, grid: tuple[int, int, int, int]) -> np.ndarray:
    gx, gy, gw, gh = grid
    green = frame[gy : gy + gh : _VIEW_STEP, gx : gx + gw : _VIEW_STEP, 1]
    out: np.ndarray = green.astype(np.float32)
    return out


def _view_still(last_view: np.ndarray, view: np.ndarray) -> bool:
    """True when the game view did not slide between the two samples (player standing)."""
    window = cv2.createHanningWindow(view.shape[::-1], cv2.CV_32F)
    (dx, dy), _ = cv2.phaseCorrelate(last_view, view, window)
    return max(abs(dx), abs(dy)) * _VIEW_STEP < _WALK_PX


def _slots(
    tops: list[tuple[int, int, float]],
    grid: tuple[int, int, int, int],
    last_tops: list[tuple[int, int, float]],
) -> dict[tuple[int, int], float]:
    """Slot -> HP of each bar top-left ``(x, y, hp)``; a bar that moved since
    *last_tops* goes on the tile ahead of it."""
    tw, th = grid[2] / _COLS, grid[3] / _ROWS
    bars: dict[tuple[int, int], float] = {}
    for x, y, hp in tops:
        ahead_x = ahead_y = 0
        near = [
            (lx, ly) for lx, ly, _ in last_tops if abs(lx - x) < tw / 2 and abs(ly - y) < th / 2
        ]
        if near:
            lx, ly = min(near, key=lambda top: abs(top[0] - x) + abs(top[1] - y))
            ahead_x = int(np.sign(x - lx)) if abs(x - lx) >= _WALK_PX else 0
            ahead_y = int(np.sign(y - ly)) if abs(y - ly) >= _WALK_PX else 0
        # Half a tile ahead (less a pixel, for a bar that just arrived) is the
        # tile the creature walks into; standing, the tile under the bar.
        col = int((x + BAR_W / 2 + ahead_x * (tw / 2 - 1)) // tw)
        row = int((y + th / 2 + ahead_y * (th / 2 - 1)) // th)
        if 0 <= col < _COLS and 0 <= row < _ROWS:
            bars[(col, row)] = hp
    bars.pop(PLAYER_SLOT, None)
    return bars


def _bar_tops(frame: np.ndarray, grid: tuple[int, int, int, int]) -> list[tuple[int, int, float]]:
    """Top-left ``(x, y)`` in the game grid and HP percent of every health bar."""
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
    return [
        (x, y, 100.0 * int((bright[y + 1, x + 1 : x + BAR_W - 1] >= _FILL).sum()) / (BAR_W - 2))
        for y, x in zip(ys.tolist(), xs.tolist(), strict=True)
    ]
