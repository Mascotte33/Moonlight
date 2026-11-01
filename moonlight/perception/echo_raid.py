"""Echo raid triggers on the game screen, found by their glowing core.

An echo raid trigger is a turning red swirl that stays on one tile until
someone walks into it, which spawns stronger monsters. Its arms turn from
frame to frame, but every frame has the same bright salmon core: light
red, orange and pink pixels with some blue in them. Fire and lava have
almost no blue; health bars and blood are darker; light wood is less red.

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
#: Core pixels per tile area that make a trigger (wiki frames 0.12-0.33, the live
#: client 0.065 in tests/echo_raid_live.png; wooden crates stay under 0.005).
CORE_SHARE = 0.04
#: The core's centre sits this far up-left of its tile's middle, in tiles.
_CORE_OFFSET = 0.35
_CLOSE_KERNEL = np.ones((3, 3), np.uint8)


#: RGB bounds of a core pixel: bright red with some blue, not white.
_CORE_LOW, _CORE_HIGH = np.array([230, 60, 30]), np.array([255, 255, 215])
#: Red minus green of a core pixel at least: light wood (crates) is 30-50, the core ~110.
_CORE_RED_OVER_GREEN = 60


def find_echo_raids(frame: np.ndarray, grid: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    """Slots ``(col, row)`` of the echo raid triggers in the game grid, sorted.

    *frame* is an RGB screenshot (as the capture gives it), *grid* the calibrated ``(x, y, w, h)``.
    """
    gx, gy, gw, gh = grid
    crop = frame[gy : gy + gh, gx : gx + gw]
    if crop.size == 0:
        return []
    core = cv2.inRange(crop, _CORE_LOW, _CORE_HIGH)
    core[crop[..., 0].astype(np.int16) - crop[..., 1] < _CORE_RED_OVER_GREEN] = 0
    if not cv2.countNonZero(core):
        return []
    mask = cv2.morphologyEx(core, cv2.MORPH_CLOSE, _CLOSE_KERNEL)
    count, _, stats, centres = cv2.connectedComponentsWithStats(mask)
    tw, th = gw / _COLS, gh / _ROWS
    slots: list[tuple[int, int]] = []
    biggest_first = sorted(range(1, count), key=lambda label: -stats[label, cv2.CC_STAT_AREA])
    for label in biggest_first:  # 0 is the background
        if stats[label, cv2.CC_STAT_AREA] < CORE_SHARE * tw * th:
            break
        cx, cy = centres[label]
        col, row = int(cx / tw + _CORE_OFFSET), int(cy / th + _CORE_OFFSET)
        next_to_a_core = any(max(abs(col - c), abs(row - r)) <= 1 for c, r in slots)
        if 0 <= col < _COLS and 0 <= row < _ROWS and not next_to_a_core:
            slots.append((col, row))  # a smaller blob next to a core is one of its rays
    return sorted(slots)
