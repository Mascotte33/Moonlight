"""The client's chase mode, read from the side-panel buttons.

# doc: docs/prs/targeting.md#chase-mode

The ``chase_buttons`` region holds Stand While Fighting (upper half) and
Chase Opponent (lower half). The active button's icon is drawn red, the other
one gray.
"""

from __future__ import annotations

import numpy as np

from moonlight.calibration.calibrate import Rect

__all__ = ["read_chase_mode"]

#: A red icon pixel: strong red, weak green and blue.
RED_MIN, OTHER_MAX = 150, 90
#: Red pixels a half needs to count as the active button.
MIN_RED_PIXELS = 10


def _red_pixels(rgb: np.ndarray) -> int:
    red = rgb[..., 0].astype(np.int16)
    other = np.maximum(rgb[..., 1], rgb[..., 2]).astype(np.int16)
    return int(((red >= RED_MIN) & (other <= OTHER_MAX)).sum())


def read_chase_mode(frame: np.ndarray, region: Rect) -> str | None:
    """``"follow"``, ``"none"`` (stand still), or None when neither icon is red."""
    x, y, w, h = region
    buttons = frame[y : y + h, x : x + w, :3]
    stand = _red_pixels(buttons[: h // 2])
    chase = _red_pixels(buttons[h // 2 :])
    if max(stand, chase) < MIN_RED_PIXELS or min(stand, chase) >= MIN_RED_PIXELS:
        return None
    return "follow" if chase > stand else "none"
