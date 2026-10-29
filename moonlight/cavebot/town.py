"""Town waypoints: what they look for on the screen when they run.

The depot lockers, the locker's stash and the loot backpack are found in the
frame by template match (like the Players window title), not in Calibration:
they are only on screen in town. Pictures are ``data/templates/anchors/``.

# doc: docs/prs/depot.md#found-on-screen
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from pathlib import Path

import cv2
import numpy as np

from moonlight.calibration.autofind import ANCHORS_DIR, MATCH_MIN

__all__ = ["find_lockers", "find_picture", "pick_locker"]

#: Tile size (px) the locker picture was cut at.
_LOCKER_TILE = 96
#: Locker picture top-left to its tile's top-left, at 96 px tiles.
_LOCKER_OFFSET = 12
_NEIGHBOURS = ((0, 1), (-1, 0), (1, 0), (0, -1), (-1, 1), (1, 1), (-1, -1), (1, -1))


@cache
def _picture(path: Path, scale: float = 1.0) -> np.ndarray:
    grey = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if grey is None:
        raise FileNotFoundError(f"missing picture: {path}")
    if scale != 1.0:
        grey = cv2.resize(grey, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return grey


def _grey(frame: np.ndarray) -> np.ndarray:
    return frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def find_lockers(
    frame: np.ndarray, grid: tuple[int, int, int, int], *, anchors: Path = ANCHORS_DIR
) -> set[tuple[int, int]]:
    """Game grid slots ``(col, row)`` of the depot lockers in view."""
    gx, gy, gw, _ = grid
    tile = gw / 15
    s = tile / _LOCKER_TILE
    scores = cv2.matchTemplate(
        _grey(frame), _picture(anchors / "depot_locker.png", round(s, 3)), cv2.TM_CCOEFF_NORMED
    )
    ys, xs = np.nonzero(scores >= MATCH_MIN)
    return {
        (round((x + _LOCKER_OFFSET * s - gx) / tile), round((y + _LOCKER_OFFSET * s - gy) / tile))
        for x, y in zip(xs.tolist(), ys.tolist(), strict=True)
    }


def find_picture(frame: np.ndarray, path: Path) -> tuple[int, int] | None:
    """Centre of the best match of the picture at *path*, or None under ``MATCH_MIN``."""
    pic = _picture(path)
    scores = cv2.matchTemplate(_grey(frame), pic, cv2.TM_CCOEFF_NORMED)
    _, best, _, (x, y) = cv2.minMaxLoc(scores)
    if best < MATCH_MIN:
        return None
    return x + pic.shape[1] // 2, y + pic.shape[0] // 2


# doc: docs/prs/depot.md#finddepo-waypoint
def pick_locker(
    lockers: set[tuple[int, int]],
    pos: tuple[int, int],
    free: Callable[[int, int], bool],
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """``(locker, stand tile)``: the free tile next to a locker nearest *pos*.

    *free* says a tile can be stood on (no creature or player, walkable).
    """
    best: tuple[int, tuple[int, int], tuple[int, int]] | None = None
    for lx, ly in sorted(lockers):
        for dx, dy in _NEIGHBOURS:
            tile = (lx + dx, ly + dy)
            if tile in lockers or not free(*tile):
                continue
            d = max(abs(tile[0] - pos[0]), abs(tile[1] - pos[1]))
            if best is None or d < best[0]:
                best = (d, (lx, ly), tile)
    return None if best is None else (best[1], best[2])
