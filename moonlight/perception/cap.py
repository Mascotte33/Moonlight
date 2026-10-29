"""Free capacity from the "Cap:" box on the right side panel (refill trigger).

Finds the ``Cap:`` label picture once (``data/templates/cap_label.png``),
then reads the number under it. The client draws the digits a little
differently depending on where they land, so each digit must match one of
the known shapes, or be ``NEAR_RATIO`` times closer to one digit than to any
other, compared blurred so a pixel more or less at an edge costs little (a
plain nearest match misread 1530 as 1850); an unknown one is logged and the
cap is unknown (no refill for it).

# doc: docs/prs/potion-count.md#cap-reader
"""

from __future__ import annotations

import logging
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
#: An unknown shape reads as the closest digit when every other digit is
#: NEAR_RATIO times further (holding out each golden shape in turn, 1.7 gave
#: one wrong digit, a 0 read as 9; 2 gave none).
NEAR_RATIO = 2.0
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
    ".##../###../.##../..#../.##../..#../.###./#####": "1",  # bot.log 2026-10-29
    ".####./.#####/.##.##/###.##/.##.##/.##.##/.#####/.####.": "0",  # bot.log 2026-10-29
    ".####./#####./##.##./.####./#####./##..##/######/#####.": "8",  # bot.log 2026-10-29
    "...##./..###./..###./.##.#./######/######/...##./....#.": "4",  # bot.log 2026-10-29
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
        digit = CAP_DIGITS.get(pattern) or _near(lit[:, x:end])
        if digit is None:
            logger.info("cap: unknown digit %s", pattern)
            return None
        text += digit
        x = end
    return int(text) if text else None


def _blurred(shape: np.ndarray, left: int, width: int) -> np.ndarray:
    placed = np.zeros((shape.shape[0], width), np.float32)
    placed[:, left : left + shape.shape[1]] = shape
    return cv2.GaussianBlur(placed, (3, 3), 0.8)


def _off(a: np.ndarray, b: np.ndarray) -> float:
    """How far apart shapes *a* and *b* are, blurred, best side-by-side shift."""
    width = max(a.shape[1], b.shape[1]) + 2
    return min(
        float(np.abs(_blurred(a, left_a, width) - _blurred(b, left_b, width)).sum())
        for left_a in range(width - a.shape[1] + 1)
        for left_b in range(width - b.shape[1] + 1)
    )


def _near(shape: np.ndarray) -> str | None:
    """The digit *shape* is clearly closest to, or None."""
    # ponytail: compares against all 50 shapes on every unknown digit; fine, unknowns are rare.
    off: dict[str, float] = {}
    for pattern, digit in CAP_DIGITS.items():
        known = np.array([[c == "#" for c in row] for row in pattern.split("/")])
        if len(digit) == 1 and known.shape[0] == shape.shape[0]:
            if abs(known.shape[1] - shape.shape[1]) <= 2:
                off[digit] = min(off.get(digit, float(known.size)), _off(shape, known))
    ranked = sorted(off.items(), key=lambda kv: kv[1])
    if len(ranked) < 2 or ranked[1][1] < NEAR_RATIO * ranked[0][1]:
        return None
    return ranked[0][0]


class CapReader:
    """Free capacity read from a full client-window grab."""

    def __init__(
        self,
        grab: Callable[[], np.ndarray],
        label: Path = CAP_LABEL,
    ) -> None:
        bgr = cv2.imread(str(label))
        if bgr is None:
            raise FileNotFoundError(f"missing cap label picture: {label}")
        self._label = np.ascontiguousarray(bgr[:, :, ::-1]).astype(np.float32)
        self._grab = grab
        self._spot: tuple[int, int] | None = None
        #: The number's pixels from the last :meth:`cap` (None: box not found).
        self.crop: np.ndarray | None = None

    def cap(self) -> int | None:
        """Free capacity, or None when the box is not found or does not read."""
        frame = self._grab()
        spot = self._find(frame)
        self.crop = None
        if spot is None:
            return None
        x, y = spot
        top, bottom = NUMBER_ROWS
        left, right = NUMBER_COLS
        self.crop = frame[y + top : y + bottom, max(0, x + left) : x + right].copy()
        return read_number(self.crop)

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
