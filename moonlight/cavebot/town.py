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

__all__ = ["BACKPACK_FILE", "STASH", "find_chat", "find_lockers", "find_picture", "pick_locker"]

#: Tile size (px) the locker picture was cut at.
_LOCKER_TILE = 96
#: Locker picture top-left to its tile's top-left, at 96 px tiles.
_LOCKER_OFFSET = 12
#: The stash item in the open locker window.
STASH = ANCHORS_DIR / "depot_stash.png"
#: The NPC trade window (its "Currency: Gold Coin" label).
TRADE = ANCHORS_DIR / "npc_trade.png"
#: The loot backpack picked on the Refill page, in the profiles folder.
BACKPACK_FILE = "loot_backpack.png"
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


def _match_scores(grey: np.ndarray, picture: np.ndarray) -> np.ndarray:
    """Match scores of *picture* over *grey*, empty when the picture is larger:
    OpenCV would then match the frame inside the picture instead."""
    if picture.shape[0] > grey.shape[0] or picture.shape[1] > grey.shape[1]:
        return np.empty((0, 0), np.float32)
    return cv2.matchTemplate(grey, picture, cv2.TM_CCOEFF_NORMED)


def find_lockers(
    frame: np.ndarray, grid: tuple[int, int, int, int], *, anchors: Path = ANCHORS_DIR
) -> set[tuple[int, int]]:
    """Game grid slots ``(col, row)`` of the depot lockers in view."""
    gx, gy, gw, _ = grid
    tile = gw / 15
    s = tile / _LOCKER_TILE
    scores = _match_scores(_grey(frame), _picture(anchors / "depot_locker.png", round(s, 3)))
    ys, xs = np.nonzero(scores >= MATCH_MIN)
    return {
        (round((x + _LOCKER_OFFSET * s - gx) / tile), round((y + _LOCKER_OFFSET * s - gy) / tile))
        for x, y in zip(xs.tolist(), ys.tolist(), strict=True)
    }


# doc: docs/prs/depot.md#chat-toggle
def find_chat(
    frame: np.ndarray, *, anchors: Path = ANCHORS_DIR
) -> tuple[bool, tuple[int, int]] | None:
    """``(chat on, button centre)`` from the chat button, or None when it is
    not found. Its looks are alike: the best match wins. ``chat_npc.png`` is
    the coloured "Chat On" of an NPC talk, whose window has a second button:
    the bottom one (the main chat bar's) is taken."""
    grey = _grey(frame)
    best: np.ndarray | None = None
    for name, on in (("chat_on.png", True), ("chat_npc.png", True), ("chat_off.png", False)):
        try:
            pic = _picture(anchors / name)
        except FileNotFoundError:
            continue  # no picture: that look is not told apart
        scores = _match_scores(grey, pic)
        if not scores.size:
            continue  # a frame smaller than the button holds no button
        if best is None:
            best, look = scores, np.full(scores.shape, on)
            half = (pic.shape[1] // 2, pic.shape[0] // 2)
        else:
            h, w = min(best.shape[0], scores.shape[0]), min(best.shape[1], scores.shape[1])
            best, look, scores = best[:h, :w], look[:h, :w], scores[:h, :w]
            look = np.where(scores > best, on, look)
            best = np.maximum(best, scores)
    if best is None:
        return None
    ys, _ = np.nonzero(best >= MATCH_MIN)
    if not len(ys):
        return None
    band = np.where(np.arange(best.shape[0])[:, None] >= ys.max() - 6, best, -1.0)
    y, x = np.unravel_index(int(np.argmax(band)), band.shape)
    return bool(look[y, x]), (int(x) + half[0], int(y) + half[1])


def find_picture(frame: np.ndarray, path: Path) -> tuple[int, int] | None:
    """Centre of the best match of the picture at *path*, or None (no file, or
    under ``MATCH_MIN``). Read each call: the owner may pick a new one."""
    pic = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if pic is None:
        return None
    scores = _match_scores(_grey(frame), pic)
    if not scores.size:
        return None
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

