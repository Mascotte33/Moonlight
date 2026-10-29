"""Offline tests for the side panel Cap reader."""

# doc: docs/prs/potion-count.md#cap-reader

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from moonlight.perception.cap import CAP_DIGITS, CapReader, read_number

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


def draw(*shapes: str) -> np.ndarray:
    """RGB pixels with *shapes* (CAP_DIGITS keys) side by side, a column apart."""
    cols = []
    for shape in shapes:
        rows = shape.split("/")
        cols.append(np.array([[c == "#" for c in row] for row in rows]))
        cols.append(np.zeros((len(rows), 1), bool))
    lit = np.hstack(cols)
    return np.where(lit[:, :, None], 220, 30).astype(np.uint8).repeat(3, axis=2)


def test_digits_must_match_exactly() -> None:
    shape = {d: g for g, d in CAP_DIGITS.items()}
    assert read_number(draw(shape["1"], shape["5"], shape["3"], shape["0"])) == 1530
    assert read_number(draw(shape["3"], shape["04"])) == 304  # touching pair
    three = shape["3"]
    off = three.index("/.") + 1
    near = three[:off] + "#" + three[off + 1 :]  # one pixel off a 3
    assert near not in CAP_DIGITS
    assert read_number(draw(shape["1"], near)) is None
