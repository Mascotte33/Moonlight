"""Find the screen areas on a screenshot by themselves.

One finder per area in :data:`FINDERS`. A finder takes a BGR screenshot and
returns the area's box, or ``None`` when it can't find it.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from pathlib import Path

import cv2
import numpy as np

from moonlight.setup.calibrate import Rect

# doc: docs/prs/gui-calibration.md#finder-registry

# HP/MP bars (moved from tools/calibrate_auto.py, checked on 200 screenshots).
SEARCH_ROWS = 80          # bars live in the top strip of the client
MIN_BORDER_RUN = 300      # px; each bar's bottom border is at least this long
MAX_BORDER_GAP = 20       # px; dark gap between the HP and MP border runs


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Return inclusive (start, end) index pairs of True runs in a 1-D mask."""
    d = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def hp_fill_mask(rgb: np.ndarray) -> np.ndarray:
    """Saturated, non-blue pixels: every HP stage (green..red)."""
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return np.asarray((sat > 40) & ((r > b + 40) | (g > b + 40)))


def mp_fill_mask(rgb: np.ndarray) -> np.ndarray:
    """Blue-dominant pixels, excluding the purple magic-shield overlay (G < R)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return (b > r + 40) & (b > g + 30) & (g >= r)


# doc: docs/prs/gui-calibration.md#bar-finders
def detect_bars(rgb: np.ndarray) -> tuple[Rect, Rect]:
    """Find the HP and MP bar boxes from the bottom border row."""
    top = rgb[:SEARCH_ROWS]
    neutral = (top.max(axis=2) - top.min(axis=2)) <= 6
    grey = neutral & (top[..., 0] >= 105) & (top[..., 0] <= 140)
    for yb in range(SEARCH_ROWS):
        runs = [(a, b) for a, b in _runs(grey[yb]) if b - a + 1 >= MIN_BORDER_RUN]
        if len(runs) == 2 and 0 < runs[1][0] - runs[0][1] <= MAX_BORDER_GAP:
            break
    else:
        raise ValueError("bar bottom border not found")
    (hx0, hx1), (mx0, mx1) = runs
    # The border's last column is the corner, not part of the bar.
    hp_cols = slice(hx0, hx1)
    mp_cols = slice(mx0, mx1)
    above = rgb[:yb]
    fill_rows = np.asarray(
        hp_fill_mask(above[:, hp_cols]).any(axis=1) | mp_fill_mask(above[:, mp_cols]).any(axis=1)
    )
    # Bar rows: the contiguous block of fill rows directly above the border.
    if not fill_rows[yb - 1]:
        raise ValueError("no bar fill directly above the border")
    y0 = yb - 1
    while y0 > 0 and fill_rows[y0 - 1]:
        y0 -= 1
    h = yb - y0
    return (hx0, y0, hx1 - hx0, h), (mx0, y0, mx1 - mx0, h)


def find_hp_bar(screenshot: np.ndarray) -> Rect | None:
    return _bars(screenshot)[0]


def find_mp_bar(screenshot: np.ndarray) -> Rect | None:
    return _bars(screenshot)[1]


def _bars(screenshot: np.ndarray) -> tuple[Rect, Rect]:
    rgb = screenshot[:SEARCH_ROWS, :, ::-1].astype(np.int16)
    return detect_bars(rgb)


# doc: docs/prs/gui-calibration.md#reference-images
ANCHORS_DIR = Path("data/templates/anchors")
MATCH_MIN = 0.8  # floor indicator scores 0.856 at worst, wrong spots under 0.5


@cache
def _anchor(name: str) -> np.ndarray | None:
    return cv2.imread(str(ANCHORS_DIR / f"{name}.png"))


def _match(screenshot: np.ndarray, name: str) -> tuple[int, int] | None:
    """Top-left of the reference image's best match, or None if missing or weak."""
    template = _anchor(name)
    if template is None:
        return None
    scores = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
    _, best, _, (x, y) = cv2.minMaxLoc(scores)
    return (x, y) if best >= MATCH_MIN else None


# The list starts 13 px below the header's top and is 156 px wide. It ends at
# the window's bottom border, a flat light-grey row.
BATTLE_TOP, BATTLE_W = 13, 156
GRIP_W = 12  # the resize grip in the bottom-left corner breaks the grey row


def find_battle_list(screenshot: np.ndarray) -> Rect | None:
    hit = _match(screenshot, "battle_list_header")
    if hit is None:
        return None
    x, y = hit
    rows = screenshot[y + BATTLE_TOP :, x + GRIP_W : x + BATTLE_W].astype(np.int16)
    bright = (rows.min(axis=(1, 2)) >= 100) & (np.ptp(rows, axis=(1, 2)) <= 30)
    if not bright.any():
        return None
    return (x, y + BATTLE_TOP, BATTLE_W, int(bright.argmax()))


Finder = Callable[[np.ndarray], Rect | None]

FINDERS: list[tuple[str, Finder]] = [
    ("hp_bar", find_hp_bar),
    ("mp_bar", find_mp_bar),
    ("battle_list", find_battle_list),
]


def find_regions(
    screenshot: np.ndarray, finders: list[tuple[str, Finder]] | None = None
) -> tuple[dict[str, Rect], list[str]]:
    """Run every finder; return the found boxes and the names not found."""
    found: dict[str, Rect] = {}
    missing: list[str] = []
    for name, finder in FINDERS if finders is None else finders:
        try:
            box = finder(screenshot)
        except Exception:
            box = None
        if box is None:
            missing.append(name)
        else:
            found[name] = box
    return found, missing
