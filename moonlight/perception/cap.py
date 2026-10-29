"""Free capacity from the "Cap:" box on the right side panel (refill trigger).

Finds the ``Cap:`` label picture once (``data/templates/cap_label.png``),
then reads the number under it. The client draws the digits a little
differently depending on where they land, so each digit must match one of
the known shapes exactly (a near match misread 1530 as 1850); an unknown
one is logged and the cap is unknown (no refill for it).

# doc: docs/prs/potion-count.md#cap-reader
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

__all__ = ["CAP_DIGITS", "CapReader", "read_number"]

logger = logging.getLogger(__name__)

CAP_LABEL = Path("data/templates/cap_label.png")
#: Largest mean squared colour difference per pixel that counts as the label.
MATCH_THRESHOLD = 200.0
#: The number under the label, from the label's top-left: rows, then columns.
NUMBER_ROWS = (9, 17)
NUMBER_COLS = (-8, 28)
#: Digit pixels: RGB sum of 300 or more.
LIT = 300
#: Known digit shapes, row by row, from the golden screenshots. Digits that
#: touch come out as one shape and stand for both.
CAP_DIGITS = {
    "####./#####/##.##/##.##/##.##/##.##/#####/####.": "0",
    ".####./#####./##.##./##..##/##..#./##.##./#####./.####.": "0",
    ".####./#####./##..##/##..##/##..##/##..##/#####./.####.": "0",
    (
        ".####....##./#####...###./##..##..###./##..##.####./"
        "##..##.#####/##..########/#####....###/.####....##."
    ): "04",
    ".##../###../.##../..#../..#../..#../.###./#####": "1",
    ".##./###./###./.##./.##./.##./####/####": "1",
    "..##./####./.###./..##./..##./..##./.####/#####": "1",
    "..##./####./.###./..##./..##./..##./.###./#####": "1",
    "#####/#####/...##/...##/..##./.###./#####/#####": "2",
    "####./#####/...##/...##/..##./.###./#####/#####": "2",
    "####./#####/...##/...##/..##./.##../#####/#####": "2",
    ".####./.####./...##./...##./...##./..##../.#####/######": "2",
    (
        ".####....##./.####...###./...##...###./...##..####./"
        "...##..#####/..##..######/.#####...###/######...##."
    ): "24",
    (
        ".####..####./.####..#####/...##.##..##/...##..##.##/"
        "...##..#####/..##......##/.#####.####./######.####."
    ): "29",
    "#####./#####./...##./..###./..###./....##/#####./#####.": "3",
    "#####/#####/...##/.###./.####/...##/#####/####.": "3",
    "#####/#####/...##/.###./..###/...##/#####/####.": "3",
    "####./#####/...##/.###./.####/...##/#####/####.": "3",
    "..###/..###/.####/##.##/#####/#####/...##/...##": "4",
    "...##./..###./.####./.#.##./##.##./######/...##./...##.": "4",
    (
        "..###..####./..###.#####./.####.##..##/##.##.##..##/"
        "#####.##..##/########..##/...##.#####./...##..####."
    ): "40",
    (
        "..###..####./..###.######/.####.###.#./##.##..####./"
        "#####..#####/########..##/...##.######/...##..####."
    ): "48",
    (
        "...##..####./..###..#####/.####.##..##/.#.##..##.##/"
        "##.##..#####/######....##/...##..####./...##..####."
    ): "49",
    "#####/#####/##.../#####/#####/...##/#####/####.": "5",
    "#####/#####/##.../#####/#.###/...##/#####/####.": "5",
    "#####/#####/##.../####./#.###/...##/#####/####.": "5",
    ".####./.####./.##.../.####./.#.###/....##/#####./#####.": "5",
    (
        ".####..####./.####..#####/.##...##..##/.####..##.##/"
        ".#.###.#####/....##....##/#####..####./#####..####."
    ): "59",
    ".####/#####/##.../#####/##.##/##.##/##.##/.####": "6",
    ".####/#####/##.../#####/##.##/##.##/##.##/.###.": "6",
    "..###./.####./###.../#####./##.###/##..##/.#####/.####.": "6",
    (
        "..###....##./.####...###./###.....###./#####..####./"
        "##.###.#####/##..########/.#####...###/.####....##."
    ): "64",
    "######/######/...##./...##./..##../..##../.##.../.##...": "7",
    "#####/#####/...##/..##./..##./.###./.##../###..": "7",
    "#####/#####/...##/..##./..##./.##../.##../##...": "7",
    "#####/##.##/##.##/####./#####/##.##/##.##/#####": "8",
    "####./##.##/##.##/####./#####/#..##/##.##/#####": "8",
    ".####./######/###.#./.####./#####./##..##/######/.####.": "8",
    ".####./######/###.#./.####./.#####/##..##/######/.####.": "8",
    (
        ".####....##./######..###./###.#...###./.####..####./"
        ".#####.#####/##..########/######...###/.####....##."
    ): "84",
    (
        ".####..####./######.#####/###.#.##..##/.####..##.##/"
        ".#####.#####/##..##....##/######.####./.####..####."
    ): "89",
    "####./#####/##.##/##.##/#####/...##/#####/####.": "9",
    ".####./#####./##..##/###.##/.#####/...##./.####./.###..": "9",
    ".####./#####./##..##/##..##/######/...##./.####./.###..": "9",
    ".###./##.##/##.##/##.##/#####/...##/#####/####.": "9",
    (
        ".####....##./#####...###./##..##..###./###.##.####./"
        ".#####.#####/...##.######/.####....###/.###.....##."
    ): "94",
    (
        ".####..####./#####..#####/##..####..##/###.##.##.##/"
        ".#####.#####/...##.....##/.####..####./.###...####."
    ): "99",
}
#: Seconds a read is reused (a ``check`` lookahead asks more than once).
READ_EVERY = 5.0


def read_number(pixels: np.ndarray) -> int | None:
    """The number drawn in *pixels* (RGB, digits only), or None."""
    lit = pixels.astype(np.int32).sum(axis=2) >= LIT
    cols = lit.any(axis=0)
    text, x = "", 0
    while x < len(cols):
        if not cols[x]:
            x += 1
            continue
        end = x
        while end < len(cols) and cols[end]:
            end += 1
        pattern = "/".join("".join("#" if v else "." for v in row) for row in lit[:, x:end])
        digit = CAP_DIGITS.get(pattern)
        if digit is None:
            logger.info("cap: unknown digit %s", pattern)
            return None
        text += digit
        x = end
    return int(text) if text else None


class CapReader:
    """Free capacity read from a full client-window grab."""

    def __init__(
        self,
        grab: Callable[[], np.ndarray],
        label: Path = CAP_LABEL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        bgr = cv2.imread(str(label))
        if bgr is None:
            raise FileNotFoundError(f"missing cap label picture: {label}")
        self._label = np.ascontiguousarray(bgr[:, :, ::-1]).astype(np.float32)
        self._grab = grab
        self._clock = clock
        self._spot: tuple[int, int] | None = None
        self._last: tuple[float, bool] | None = None

    def cap(self) -> int | None:
        """Free capacity, or None when the box is not found or does not read."""
        frame = self._grab()
        spot = self._find(frame)
        if spot is None:
            return None
        x, y = spot
        top, bottom = NUMBER_ROWS
        left, right = NUMBER_COLS
        return read_number(frame[y + top : y + bottom, max(0, x + left) : x + right])

    def low(self, below: int) -> bool:
        """True when the read cap is under *below*."""
        now = self._clock()
        if self._last is not None and now - self._last[0] < READ_EVERY:
            return self._last[1]
        cap = self.cap()
        if cap is None:
            logger.info("cap: unknown, no refill for it")
        elif cap < below:
            logger.info("cap: %d, under %d", cap, below)
        due = cap is not None and cap < below
        self._last = (now, due)
        return due

    def _find(self, frame: np.ndarray) -> tuple[int, int] | None:
        h, w = self._label.shape[:2]
        pixels = float(h * w)
        if self._spot is not None:
            x, y = self._spot
            here = frame[y : y + h, x : x + w].astype(np.float32)
            if here.shape == self._label.shape and (
                float(((here - self._label) ** 2).sum()) / pixels < MATCH_THRESHOLD
            ):
                return self._spot
            self._spot = None
        scores = cv2.matchTemplate(frame.astype(np.float32), self._label, cv2.TM_SQDIFF)
        _, _, (x, y), _ = cv2.minMaxLoc(scores)
        if scores[y, x] / pixels >= MATCH_THRESHOLD:
            return None
        self._spot = (x, y)
        return x, y
