"""Stamina from the Skills window (relog trigger).

The Skills window must be open. Its ``Stamina`` line is found by the word's
lit pixels (once, then checked in place) and the ``HH:MM`` at the right end
of that line is read from the window's digit font.

# doc: docs/prs/client-hotkeys.md#stamina

A stamina that cannot be read (window closed, unknown glyph) is None and is
logged; it never counts as low.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import cv2
import numpy as np

__all__ = ["LOW_STAMINA_MINUTES", "StaminaReader", "read_stamina_minutes"]

logger = logging.getLogger(__name__)

#: Under 39:30 the character gets less experience: relog to another one.
LOW_STAMINA_MINUTES = 39 * 60 + 30
#: Skills window text: pixels with an RGB sum of 450 or more.
_LIT = 450
#: The word ``Stamina`` as the Skills window draws it (8 rows).
_LABEL = np.array(
    [
        [c == "#" for c in row]
        for row in (
            ".####...##.......................##................",
            "##...#..##.........................................",
            "##.....#####..####...#####.###...##..#####....####.",
            "#####...##.......##..##..##..##..##..##..##......##",
            ".#####..##....#####..##..##..##..##..##..##...#####",
            "....##..##...##..##..##..##..##..##..##..##..##..##",
            "#...##..##...##..##..##..##..##..##..##..##..##..##",
            ".####....###..#####..##..##..##..##..##..##...#####",
        )
    ]
)
_DIGITS = {
    ".####./##..##/##..##/##..##/##..##/##..##/##..##/.####.": "0",
    ".##./###./.##./.##./.##./.##./.##./####": "1",
    ".####./##..##/....##/...##./..##../.##.../##..../######": "2",
    ".####./##..##/....##/..###./....##/....##/##..##/.####.": "3",
    "....#./...##./..###./.#.##./#..##./######/...##./...##.": "4",
    ".#####/.##.../.##.../.####./....##/....##/##..##/.####.": "5",
    "..###./.##.../##..../#####./##..##/##..##/##..##/.####.": "6",
    "######/....##/...##./...##./..##../..##../.##.../.##...": "7",
    ".####./##..##/##..##/.####./##..##/##..##/##..##/.####.": "8",
    ".####./##..##/##..##/##..##/.#####/....##/...##./.###..": "9",
    "../../##/##/../../##/##": ":",
}


def _find_label(lit: np.ndarray) -> tuple[int, int] | None:
    """Top-left of the ``Stamina`` word in the lit-pixel mask, or None."""
    word = _LABEL.astype(np.float32) * 2 - 1  # lit +1, dark -1
    scores = cv2.matchTemplate(lit.astype(np.float32), word, cv2.TM_CCORR)
    _, best, _, (x, y) = cv2.minMaxLoc(scores)
    return (x, y) if best >= _LABEL.sum() else None


def _read_time(lit: np.ndarray) -> str | None:
    """The first five glyphs (``HH:MM``) of an 8-row text strip, or None."""
    cols = np.asarray(lit.any(axis=0))
    text, x = "", 0
    while x < len(cols) and len(text) < 5:
        if not cols[x]:
            x += 1
            continue
        end = x
        while end < len(cols) and cols[end]:
            end += 1
        glyph = "/".join("".join("#" if v else "." for v in row) for row in lit[:, x:end])
        if glyph not in _DIGITS:
            logger.info("stamina: unknown glyph %s", glyph)
            return None
        text += _DIGITS[glyph]
        x = end
    return text


def read_stamina_minutes(frame: np.ndarray, label: tuple[int, int]) -> int | None:
    """Stamina in minutes from the line whose ``Stamina`` word is at *label*."""
    x, y = label
    strip = frame[y : y + 8, x + _LABEL.shape[1] :].astype(np.int32).sum(axis=2) >= _LIT
    text = _read_time(strip)
    if text is None or len(text) != 5 or text[2] != ":" or ":" in text[:2] + text[3:]:
        return None
    return int(text[:2]) * 60 + int(text[3:])


class StaminaReader:
    """Stamina minutes read from a full client-window grab; logs each change."""

    def __init__(self, grab: Callable[[], np.ndarray]) -> None:
        self._grab = grab
        self._label: tuple[int, int] | None = None
        self._last: int | None = None

    def minutes(self) -> int | None:
        """Stamina in minutes, or None when the Skills window does not show it."""
        frame = self._grab()
        lit = frame.astype(np.int32).sum(axis=2) >= _LIT
        if self._label is not None:
            x, y = self._label
            here = lit[y : y + _LABEL.shape[0], x : x + _LABEL.shape[1]]
            if here.shape != _LABEL.shape or not (here == _LABEL).all():
                self._label = None
        if self._label is None:
            self._label = _find_label(lit)
        minutes = None if self._label is None else read_stamina_minutes(frame, self._label)
        if minutes != self._last:
            if minutes is None:
                logger.warning("stamina: not read (open the Skills window)")
            else:
                low = " (under 39:30)" if minutes < LOW_STAMINA_MINUTES else ""
                logger.info("stamina %d:%02d%s", minutes // 60, minutes % 60, low)
            self._last = minutes
        return minutes
