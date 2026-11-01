"""Echo raid triggers on the game screen, found by their glowing core.

An echo raid trigger is a turning red swirl that stays on one tile until
someone walks into it, which spawns stronger monsters. Its arms turn from
frame to frame, but every frame has the same bright salmon core: light
red, orange and pink pixels with some blue in them. Fire and lava have
almost no blue; health bars and blood are darker.

:func:`find_echo_raids` returns the game-grid slot ``(col, row)`` of each
core big enough to be a trigger. The sprite is 2x2 tiles drawn up and to
the left of its own tile, so the core's centre lies just inside the
top-left corner of that tile.

# doc: docs/prs/echo-raid.md#swirl-scan
"""

from __future__ import annotations

import cv2
import numpy as np

__all__ = ["CORE_SHARE", "find_echo_raids"]

_COLS, _ROWS = 15, 11
#: Core pixels per tile area that make a trigger (the wiki frames have 0.14-0.37).
CORE_SHARE = 0.1
#: The core's centre sits this far up-left of its tile's middle, in tiles.
_CORE_OFFSET = 0.35
_CLOSE_KERNEL = np.ones((3, 3), np.uint8)


#: BGR bounds of a core pixel: bright red with some blue, not white.
_CORE_LOW, _CORE_HIGH = np.array([30, 60, 230]), np.array([215, 255, 255])


def find_echo_raids(frame: np.ndarray, grid: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    """Slots ``(col, row)`` of the echo raid triggers in the game grid, sorted.

    *frame* is a BGR screenshot, *grid* the calibrated ``(x, y, w, h)``.
    """
    gx, gy, gw, gh = grid
    crop = frame[gy : gy + gh, gx : gx + gw]
    if crop.size == 0:
        return []
    core = cv2.inRange(crop, _CORE_LOW, _CORE_HIGH)
    if not cv2.countNonZero(core):
        return []
    mask = cv2.morphologyEx(core, cv2.MORPH_CLOSE, _CLOSE_KERNEL)
    count, _, stats, centres = cv2.connectedComponentsWithStats(mask)
    tw, th = gw / _COLS, gh / _ROWS
    slots = set()
    for label in range(1, count):  # 0 is the background
        if stats[label, cv2.CC_STAT_AREA] < CORE_SHARE * tw * th:
            continue
        cx, cy = centres[label]
        col, row = int(cx / tw + _CORE_OFFSET), int(cy / th + _CORE_OFFSET)
        if 0 <= col < _COLS and 0 <= row < _ROWS:
            slots.add((col, row))
    return sorted(slots)
