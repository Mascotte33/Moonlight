"""Offline tests for the side panel Cap reader."""

# doc: docs/prs/potion-count.md#cap-reader

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from moonlight.perception.cap import CapReader

#: A real crop of the side panel: Soul 100, Cap 985.
PANEL = Path(__file__).parent / "cap_panel.png"


def panel_in_frame(x: int, y: int) -> np.ndarray:
    bgr = cv2.imread(str(PANEL))
    assert bgr is not None
    panel = bgr[:, :, ::-1]
    frame = np.zeros((400, 600, 3), np.uint8)
    frame[y : y + panel.shape[0], x : x + panel.shape[1]] = panel
    return frame


def test_reads_the_cap_and_finds_a_moved_panel() -> None:
    frames = [panel_in_frame(400, 300)]
    reader = CapReader(lambda: frames[0])
    assert reader.cap() == 985
    frames[0] = panel_in_frame(10, 20)
    assert reader.cap() == 985
    frames[0] = np.zeros((400, 600, 3), np.uint8)
    assert reader.cap() is None


def test_low_is_cached_and_needs_a_read_cap() -> None:
    now = [0.0]
    frames = [panel_in_frame(0, 0)]
    reader = CapReader(lambda: frames[0], clock=lambda: now[0])
    assert reader.low(1000)
    frames[0] = np.zeros((400, 600, 3), np.uint8)
    assert reader.low(1000)  # reused for 5 s
    now[0] = 5.0
    assert not reader.low(1000)  # unknown cap never triggers
    assert not CapReader(lambda: panel_in_frame(0, 0)).low(900)
