"""Potion counts from the action bar (refill trigger).

Finds each saved potion picture on the action bar and reads the count in
the slot's bottom-right corner from the bar's digit font. Pictures are
``data/templates/potions/<health|mana|spirit>/*.png``: a whole action bar
slot (RGBA, the hotkey label and count masked out). Spirit potions count for
both. A slot is searched for on the whole window once and then checked in
place; it is searched again when the potion there changed.

The game draws a stack by its size (a few potions are one small flask), so a
slot found once keeps being read in place when its picture stops matching
and it is not found elsewhere. A picture named ``*_empty.png`` (the faded,
checkered slot of a potion you have none of) counts 0.

# doc: docs/prs/potion-count.md#potion-sprites

A count that cannot be read is unknown: it never triggers a refill, only a
log line (an unknown digit is logged as its picture, to add to ``DIGITS``).

# doc: docs/prs/potion-count.md#potion-count
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from moonlight.perception.pictures import for_this_os

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
    "###./..##/...#/..#./.#../####": "2",  # Linux client, 21 health potions 2026-11-21
    "###./.##./.##./.##./.##./####": "1",  # Linux client, 21 health potions
    ".##./.##./.##./.##./####/....": "1",  # Linux client (a row higher)
    "###./..#./..#./..#./..#./####": "1",  # Linux client
    "####/...#/.###/...#/...#/###.": "3",  # Linux client
    ".###./#.##./#####/..##./..##./.....": "4",  # Linux client 2026-11-21
    "..##./.###./#.##./#####/..##./..##.": "4",  # Linux client 2026-11-22
    "#..#/###./#.##/#..#/###./....": "8",  # cut off at the slot edge, 2026-11-25
    # Linux client 2026-11-25: digits a row higher or cut at the slot edge
    "####/...#/..##/..#./.##./.#..": "7",  # slanted, 77 health potions
    "...#./..##./.#.#./#####/...##/...#.": "4",  # 114 mana potions
    "..##/..#./.##./.#../##../....": "7",
    "###./#..#/###./#.##/#..#/###.": "8",
    "##./.#./.#./.#./.#./###": "1",
    ".#./.#./.#./.#./###/...": "1",
    "..##/..##/..#./##../####/....": "2",  # Linux client 2026-11-21
    ".##./###./.##./.##./.##./####": "1",
    "###./#..#/#..#/#..#/#..#/####": "0",
    "####/#.../###./..##/...#/###.": "5",
    ".####/.#..#/.####/.#.##/##..#/.####": "8",
    ".##./.##./.##./.##./.##./####": "1",
    "###./..##/..##/..#./.#../####": "2",
    "###./...#/.##./..##/...#/####": "3",
    "...#./..##./.#.#./#..#./#####/...#.": "4",
    "..###./.#.##./#..##./######/...##./...##.": "4",
    ".###/#.../####/#..#/#..#/####": "6",
    "####/...#/..#./.##./.#../##..": "7",
    "###./#..#/#..#/####/..##/###.": "9",
}


# doc: docs/prs/potion-count.md#nearest-shape
#: The Linux client draws the count digits in many shapes (slanted, a pixel
#: wider, a row off). Each glyph takes the digit of the closest known shape:
#: at most this share of its lit pixels may differ...
NEAREST_SHARE = 0.3
#: ...and the next closest other digit must be this many pixels further.
NEAREST_MARGIN = 2
#: Glyphs with fewer lit pixels are specks of the potion picture, not digits.
MIN_GLYPH_PIXELS = 4


def _bitmap(glyph: str) -> np.ndarray:
    return np.array([[c == "#" for c in row] for row in glyph.split("/")])


def _distance(a: np.ndarray, b: np.ndarray) -> int:
    """Differing pixels of two 6-row glyphs, at the best sideways offset."""
    width = max(a.shape[1], b.shape[1]) + 1
    best = a.size + b.size
    for da in range(width - a.shape[1] + 1):
        for db in range(width - b.shape[1] + 1):
            pa = np.zeros((a.shape[0], width), dtype=bool)
            pb = np.zeros((b.shape[0], width), dtype=bool)
            pa[:, da : da + a.shape[1]] = a
            pb[:, db : db + b.shape[1]] = b
            best = min(best, int((pa != pb).sum()))
    return best


#: Every known shape as a bitmap, for :func:`_nearest`.
_SHAPES = [(_bitmap(g), d) for g, d in DIGITS.items() if g.count("/") == 5 and d.isdigit()]


def _nearest(glyph: np.ndarray) -> tuple[str | None, int]:
    """The digit of the closest known shape and how far it is (None: too far or unsure)."""
    best: dict[str, int] = {}
    for shape, digit in _SHAPES:
        if shape.shape[0] == glyph.shape[0]:
            best[digit] = min(best.get(digit, 1 << 30), _distance(glyph, shape))
    if not best:
        return None, 1 << 30
    ranked = sorted(best.items(), key=lambda kv: kv[1])
    digit, dist = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 1 << 30
    if dist > NEAREST_SHARE * glyph.sum() or runner_up - dist < NEAREST_MARGIN:
        return None, dist
    return digit, dist


def read_count(slot: np.ndarray) -> int | None:
    """The count in the bottom right of an action bar *slot* (RGB), or None.

    The rows one up and one down are tried too (a client draws the count a
    row off now and then); the rows whose glyphs all read and match closest win.
    """
    best: tuple[int, str] | None = None
    unknown = ""
    for shift in (0, -1, 1):
        rows = slice(COUNT_ROWS.start + shift, COUNT_ROWS.stop + shift)
        lit = slot[rows].astype(np.int32).sum(axis=2) >= 400
        text, cost, unknown = _read_rows(lit)
        if text and (best is None or cost < best[0]):
            best = (cost, text)
        if text and cost == 0:
            break
    if best is None:
        logger.info("potions: unknown digit %s", unknown)
        return None
    return int(best[1])


def _read_rows(lit: np.ndarray) -> tuple[str | None, int, str]:
    """The digits in *lit*, how far from known shapes, and the last glyph that did not read."""
    cols = lit.any(axis=0)
    text, cost, x = "", 0, 0
    while x < len(cols):
        if not cols[x]:
            x += 1
            continue
        end = x
        while end < len(cols) and cols[end]:
            end += 1
        part = lit[:, x:end]
        x = end
        if part.sum() < MIN_GLYPH_PIXELS:
            continue
        glyph = "/".join("".join("#" if v else "." for v in row) for row in part)
        if glyph in DIGITS:
            if not DIGITS[glyph].isdigit():
                return None, 0, glyph
            text += DIGITS[glyph]
            continue
        digit, dist = _nearest(part)
        if digit is None:
            return None, 0, glyph
        text += digit
        cost += dist
    return text or None, cost, ""


class PotionCounter:
    """Health and mana potion totals read from a full client-window grab."""

    def __init__(
        self,
        grab: Callable[[], np.ndarray],
        templates_dir: Path = POTION_TEMPLATES_DIR,
    ) -> None:
        self._grab = grab
        #: (type, name, rgb, mask, weight sum)
        self._kinds: list[tuple[str, str, np.ndarray, np.ndarray, float]] = []
        for kind in TYPES:
            for shared in sorted((templates_dir / kind).glob("*.png")):
                path = for_this_os(shared)
                bgra = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                if bgra is None or bgra.ndim != 3 or bgra.shape[2] != 4:
                    raise FileNotFoundError(f"missing or non-RGBA potion picture: {path}")
                rgb = np.ascontiguousarray(bgra[:, :, 2::-1]).astype(np.float32)
                weight = bgra[:, :, 3].astype(np.float32) / 255.0
                mask = np.repeat(weight[:, :, None], 3, axis=2)
                self._kinds.append((kind, f"{kind}/{path.stem}", rgb, mask, float(weight.sum())))
        self._slots: dict[str, tuple[int, int]] = {}  # name -> slot top-left
        #: ``"health"`` / ``"mana"`` -> the slot picture the last
        #: :meth:`counts` read for it (the first found; a spirit slot counts
        #: for both).
        self.crops: dict[str, np.ndarray] = {}

    def counts(self) -> dict[str, int | None]:
        """``{"health": n, "mana": n}``; None when unknown (no potion found
        on the bar, or a count that does not read)."""
        frame = self._grab()
        totals: dict[str, int | None] = {"health": None, "mana": None}
        unknown: set[str] = set()
        self.crops = {}
        for kind, name, rgb, mask, weight in self._kinds:
            h, w = rgb.shape[:2]
            spot = self._find(frame, name, rgb, mask, weight)
            if spot is not None:
                x, y = spot
                pic = frame[y : y + h, x : x + w].copy()
                n = 0 if name.endswith("_empty") else read_count(pic)
            elif name in self._slots:  # seen before: the stack changed its picture
                # ponytail: trusts the slot still holds this potion; an item
                # moved into it is read as the potion until the bot restarts.
                x, y = self._slots[name]
                pic = frame[y : y + h, x : x + w].copy()
                n = read_count(pic)
                if n is None:
                    continue
            else:
                continue
            for t in ("health", "mana") if kind == "spirit" else (kind,):
                self.crops.setdefault(t, pic)
                if n is None:
                    unknown.add(t)
                else:
                    totals[t] = (totals[t] or 0) + n
        return {t: None if t in unknown else n for t, n in totals.items()}

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
        scores = cv2.matchTemplate(frame.astype(np.float32), rgb, cv2.TM_SQDIFF, mask=mask)
        _, _, (x, y), _ = cv2.minMaxLoc(scores)
        if scores[y, x] / weight >= MATCH_THRESHOLD:
            return None  # the last spot stays in _slots, read in place by counts()
        self._slots[name] = (x, y)
        return x, y
