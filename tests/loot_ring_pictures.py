"""Test pictures of the loot ring: its real animation frames painted on a frame."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

BLUE = (160, 178, 205)  # RGB ring dot
_STRIP = cv2.imread(
    str(Path(__file__).parents[1] / "moonlight" / "perception" / "loot_ring.png"),
    cv2.IMREAD_GRAYSCALE,
)
assert _STRIP is not None


def paint_ring(frame: np.ndarray, x: int, y: int, phase: int, tile: int = 96) -> np.ndarray:
    """Paint ring animation frame *phase* (0-15) on the *tile* px tile whose top-left is x, y."""
    ring = _STRIP[:, 32 * phase : 32 * phase + 32]
    big = cv2.resize(ring, (tile, tile), interpolation=cv2.INTER_NEAREST) > 0
    h, w = frame.shape[:2]
    cut = big[: h - y, : w - x]
    frame[y : y + cut.shape[0], x : x + cut.shape[1]][cut] = BLUE
    return frame
