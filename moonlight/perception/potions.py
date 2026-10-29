"""Potion counts from the action bar (refill trigger).

Finds each saved potion picture on the action bar and reads the count in
the slot's bottom-right corner from the bar's digit font. Pictures are
``data/templates/potions/<health|mana|spirit>/*.png``: a whole action bar
slot (RGBA, the hotkey label and count masked out). Spirit potions count for
both. A slot is searched for on the whole window once and then checked in
place; it is searched again when the potion there changed.

A count that cannot be read is unknown: it never triggers a refill, only a
log line (an unknown digit is logged as its picture, to add to ``DIGITS``).

# doc: docs/prs/potion-count.md#potion-count
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

__all__ = ["DIGITS", "POTION_TEMPLATES_DIR", "PotionCounter", "read_count"]

logger = logging.getLogger(__name__)

POTION_TEMPLATES_DIR = Path("data/templates/potions")
TYPES = ("health", "mana", "spirit")
#: Largest mean squared colour difference per mask pixel that counts as a match.
MATCH_THRESHOLD = 200.0
#: Rows of the count digits inside a slot picture.
COUNT_ROWS = slice(24, 30)
#: Action bar count digits: pixels with an RGB sum of 400 or more, row by row.
#: The game's count text is white with coloured edges, the potions are darker.
DIGITS = {
    ".##./###./.##./.##./.##./####": "1",
    "###./#..#/#..#/#..#/#..#/####": "0",
    "####/#.../###./..##/...#/###.": "5",
    ".####/.#..#/.####/.#.##/##..#/.####": "8",
}
#: Seconds a read is reused (a ``check`` lookahead asks more than once).
READ_EVERY = 5.0


def read_count(slot: np.ndarray) -> int | None:
    """The count in the bottom right of an action bar *slot* (RGB), or None."""
    lit = slot[COUNT_ROWS].astype(np.int32).sum(axis=2) >= 400
    cols = lit.any(axis=0)
    text, x = "", 0
    while x < len(cols):
        if not cols[x]:
            x += 1
            continue
        end = x
        while end < len(cols) and cols[end]:
            end += 1
        glyph = "/".join("".join("#" if v else "." for v in row) for row in lit[:, x:end])
        if glyph not in DIGITS:
            logger.info("potions: unknown digit %s", glyph)
            return None
        text += DIGITS[glyph]
        x = end
    return int(text) if text else None


class PotionCounter:
    """Health and mana potion totals read from a full client-window grab."""

    def __init__(
        self,
        grab: Callable[[], np.ndarray],
        templates_dir: Path = POTION_TEMPLATES_DIR,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._grab = grab
        self._clock = clock
        #: (type, name, rgb, mask, weight sum)
        self._kinds: list[tuple[str, str, np.ndarray, np.ndarray, float]] = []
        for kind in TYPES:
            for path in sorted((templates_dir / kind).glob("*.png")):
                bgra = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                if bgra is None or bgra.ndim != 3 or bgra.shape[2] != 4:
                    raise FileNotFoundError(f"missing or non-RGBA potion picture: {path}")
                rgb = np.ascontiguousarray(bgra[:, :, 2::-1]).astype(np.float32)
                weight = bgra[:, :, 3].astype(np.float32) / 255.0
                mask = np.repeat(weight[:, :, None], 3, axis=2)
                self._kinds.append((kind, path.stem, rgb, mask, float(weight.sum())))
        self._slots: dict[str, tuple[int, int]] = {}  # name -> slot top-left
        self._last: tuple[float, bool] | None = None

    def counts(self) -> dict[str, int | None]:
        """``{"health": n, "mana": n}``; None when unknown (no potion found
        on the bar, or a count that does not read)."""
        frame = self._grab()
        totals: dict[str, int | None] = {"health": None, "mana": None}
        unknown: set[str] = set()
        for kind, name, rgb, mask, weight in self._kinds:
            spot = self._find(frame, name, rgb, mask, weight)
            if spot is None:
                continue
            x, y = spot
            h, w = rgb.shape[:2]
            n = read_count(frame[y : y + h, x : x + w])
            for t in ("health", "mana") if kind == "spirit" else (kind,):
                if n is None:
                    unknown.add(t)
                else:
                    totals[t] = (totals[t] or 0) + n
        return {t: None if t in unknown else n for t, n in totals.items()}

    def low(self, health_below: int, mana_below: int) -> bool:
        """True when a known count is under its threshold (0 = off)."""
        now = self._clock()
        if self._last is not None and now - self._last[0] < READ_EVERY:
            return self._last[1]
        counts = self.counts()
        due = False
        for t, below in (("health", health_below), ("mana", mana_below)):
            n = counts[t]
            if below <= 0:
                continue
            if n is None:
                logger.info("potions: %s count unknown, no refill for it", t)
            elif n < below:
                logger.info("potions: %d %s potions, under %d", n, t, below)
                due = True
        self._last = (now, due)
        return due

    def _find(
        self, frame: np.ndarray, name: str, rgb: np.ndarray, mask: np.ndarray, weight: float
    ) -> tuple[int, int] | None:
        h, w = rgb.shape[:2]
        spot = self._slots.get(name)
        if spot is not None:
            x, y = spot
            here = frame[y : y + h, x : x + w].astype(np.float32)
            if here.shape == rgb.shape:
                score = cv2.matchTemplate(here, rgb, cv2.TM_SQDIFF, mask=mask)[0, 0]
                if score / weight < MATCH_THRESHOLD:
                    return spot
            del self._slots[name]
        scores = cv2.matchTemplate(frame.astype(np.float32), rgb, cv2.TM_SQDIFF, mask=mask)
        _, _, (x, y), _ = cv2.minMaxLoc(scores)
        if scores[y, x] / weight >= MATCH_THRESHOLD:
            return None
        self._slots[name] = (x, y)
        return x, y
