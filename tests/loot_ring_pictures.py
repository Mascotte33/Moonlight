"""Test pictures of the loot ring: its real animation frames painted on a frame."""

from __future__ import annotations

import cv2
import numpy as np

from moonlight.perception.corpses import _RING_FRAMES

BLUE = (160, 178, 205)  # RGB ring dot


def paint_ring(frame: np.ndarray, x: int, y: int, phase: int, tile: int = 96) -> np.ndarray:
    """Paint ring animation frame *phase* (0-15) on the *tile* px tile whose top-left is x, y."""
    ring = _RING_FRAMES[phase]
    big = cv2.resize(ring, (tile, tile), interpolation=cv2.INTER_NEAREST) > 0
    h, w = frame.shape[:2]
    cut = big[: h - y, : w - x]
    frame[y : y + cut.shape[0], x : x + cut.shape[1]][cut] = BLUE
    return frame
