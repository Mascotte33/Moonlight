"""Stamina and Level from the Skills window (relog trigger, cavebot rotate).

The Skills window must be open. Its ``Stamina`` line is found by the word's
lit pixels (once, then checked in place) and the ``HH:MM`` at the right end
of that line is read from the window's digit font. The ``Level`` line is read
the same way (:class:`LevelReader`).

# doc: docs/prs/client-hotkeys.md#stamina
# doc: docs/prs/cavebot-rotate.md#level-read

A stamina that cannot be read (window closed, unknown glyph) is None and is
logged; it never counts as low.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import cv2
import numpy as np

__all__ = ["LOW_STAMINA_MINUTES", "LevelReader", "StaminaReader", "read_stamina_minutes"]

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
#: The word ``Level`` as the Skills window draws it (9 rows: the ``l`` is tall).
_LEVEL_LABEL = np.array(
    [
        [c == "#" for c in row]
        for row in (
            "...............................##",
            "##.............................##",
            "##.............................##",
            "##......####...##..##...####...##",
            "##.....##..##..##..##..##..##..##",
            "##.....######..##..##..######..##",
            "##.....##.......####...##......##",
            "##.....##...#...####...##...#..##",
            "######..####.....##.....####...##",
        )
    ]
)
# doc: docs/prs/stamina-switch.md#tight-font
#: The same words as some clients draw them: one column less between letters.
_LABEL_TIGHT = np.array(
    [
        [c == "#" for c in row]
        for row in (
            ".####..##......................##................",
            "##...#.##........................................",
            "##....#####..####...#####.###..##..#####....####.",
            "#####..##.......##..##..##..##.##..##..##......##",
            ".#####.##....#####..##..##..##.##..##..##...#####",
            "....##.##...##..##..##..##..##.##..##..##..##..##",
            "#...##.##...##..##..##..##..##.##..##..##..##..##",
            ".####...###..#####..##..##..##.##..##..##...#####",
        )
    ]
)
_LEVEL_LABEL_TIGHT = np.array(
    [
        [c == "#" for c in row]
        for row in (
            "............................##",
            "##..........................##",
            "##..........................##",
            "##......####..##..##..####..##",
            "##.....##..##.##..##.##..##.##",
            "##.....######.##..##.######.##",
            "##.....##......####..##.....##",
            "##.....##...#..####..##...#.##",
            "######..####....##....####..##",
        )
    ]
)
_LABELS = (_LABEL, _LABEL_TIGHT)
_LEVEL_LABELS = (_LEVEL_LABEL, _LEVEL_LABEL_TIGHT)
#: Where a word was found and which of its drawings it is.
_Found = tuple[tuple[int, int], np.ndarray]

#: Empty columns that still separate two digits of one number (the window's
#: scroll marks stand further right).
_DIGIT_GAP = 3
_MAX_LEVEL_DIGITS = 5
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


def _find_label(lit: np.ndarray, label: np.ndarray) -> tuple[int, int] | None:
    """Top-left of the word *label* in the lit-pixel mask, or None."""
    word = label.astype(np.float32) * 2 - 1  # lit +1, dark -1
    scores = cv2.matchTemplate(lit.astype(np.float32), word, cv2.TM_CCORR)
    _, best, _, (x, y) = cv2.minMaxLoc(scores)
    return (x, y) if best > label.sum() - 0.5 else None  # float sums


def _locate(
    lit: np.ndarray, label: np.ndarray, cached: tuple[int, int] | None
) -> tuple[int, int] | None:
    """Where *label* is: the *cached* spot while it still shows the word, else a new search."""
    if cached is not None:
        x, y = cached
        here = lit[y : y + label.shape[0], x : x + label.shape[1]]
        if here.shape == label.shape and (here == label).all():
            return cached
    return _find_label(lit, label)


def _locate_any(
    lit: np.ndarray, labels: tuple[np.ndarray, ...], cached: _Found | None
) -> _Found | None:
    """The first drawing of a word in *labels* that shows, and where (*cached* first)."""
    if cached is not None and _locate(lit, cached[1], cached[0]) == cached[0]:
        return cached
    for label in labels:
        spot = _find_label(lit, label)
        if spot is not None:
            return spot, label
    return None


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


def read_stamina_minutes(
    frame: np.ndarray, label: tuple[int, int], width: int = _LABEL.shape[1]
) -> int | None:
    """Stamina in minutes from the line whose ``Stamina`` word (*width* wide) is at *label*."""
    x, y = label
    strip = frame[y : y + 8, x + width :].astype(np.int32).sum(axis=2) >= _LIT
    text = _read_time(strip)
    if text is None or len(text) != 5 or text[2] != ":" or ":" in text[:2] + text[3:]:
        return None
    return int(text[:2]) * 60 + int(text[3:])


def read_level(
    frame: np.ndarray, label: tuple[int, int], width: int = _LEVEL_LABEL.shape[1]
) -> int | None:
    """The number right of the ``Level`` word (*width* wide) at *label* (up to a wide gap)."""
    x, y = label
    # The digits are 8 rows high and start one row below the tall ``l`` of the word.
    strip = frame[y + 1 : y + 9, x + width :].astype(np.int32).sum(axis=2) >= _LIT
    cols = np.asarray(strip.any(axis=0))
    text, col = "", 0
    while col < len(cols) and len(text) < _MAX_LEVEL_DIGITS:
        if not cols[col:].any() or (text and not cols[col : col + _DIGIT_GAP + 1].any()):
            break
        if not cols[col]:
            col += 1
            continue
        end = col
        while end < len(cols) and cols[end]:
            end += 1
        glyph = "/".join("".join("#" if v else "." for v in row) for row in strip[:, col:end])
        digit = _DIGITS.get(glyph)
        if digit is None or digit == ":":
            logger.info("level: unknown glyph %s", glyph)
            return None
        text += digit
        col = end
    return int(text) if text else None


class LevelReader:
    """Character level read from a full client-window grab; logs each change."""

    def __init__(self, grab: Callable[[], np.ndarray]) -> None:
        self._grab = grab
        self._label: _Found | None = None
        self._last: int | None = None

    def level(self) -> int | None:
        """The level, or None when the Skills window does not show it."""
        frame = self._grab()
        lit = frame.astype(np.int32).sum(axis=2) >= _LIT
        self._label = found = _locate_any(lit, _LEVEL_LABELS, self._label)
        level = None if found is None else read_level(frame, found[0], found[1].shape[1])
        if level != self._last:
            if level is None:
                logger.warning("level: not read (open the Skills window)")
            else:
                logger.info("level %d", level)
            self._last = level
        return level


class StaminaReader:
    """Stamina minutes read from a full client-window grab; logs each change."""

    def __init__(self, grab: Callable[[], np.ndarray]) -> None:
        self._grab = grab
        self._label: _Found | None = None
        self._last: int | None = None

    def minutes(self) -> int | None:
        """Stamina in minutes, or None when the Skills window does not show it."""
        frame = self._grab()
        lit = frame.astype(np.int32).sum(axis=2) >= _LIT
        self._label = found = _locate_any(lit, _LABELS, self._label)
        minutes = (
            None if found is None else read_stamina_minutes(frame, found[0], found[1].shape[1])
        )
        if minutes != self._last:
            if minutes is None:
                logger.warning("stamina: not read (open the Skills window)")
            else:
                low = " (under 39:30)" if minutes < LOW_STAMINA_MINUTES else ""
                logger.info("stamina %d:%02d%s", minutes // 60, minutes % 60, low)
            self._last = minutes
        return minutes
