"""Find the screen areas on a screenshot by themselves.

One finder per area in :data:`FINDERS`. A finder takes a BGR screenshot and
returns the area's box, or ``None`` when it can't find it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import cache
from pathlib import Path

import cv2
import numpy as np

from moonlight.calibration.calibrate import Point, Rect
from moonlight.perception.battle import TOP_PADDING
from moonlight.perception.vitals import hp_fill_mask

# doc: docs/prs/gui-calibration.md#finder-registry

# HP/MP bars (moved from tools/calibrate_auto.py, checked on 200 screenshots).
SEARCH_ROWS = 80  # bars live in the top strip of the client
MIN_BORDER_RUN = 300  # px; each bar's bottom border is at least this long
MAX_BORDER_GAP = 20  # px; dark gap between the HP and MP border runs


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Return inclusive (start, end) index pairs of True runs in a 1-D mask."""
    d = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


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
MATCH_MIN = 0.8  # floor indicator scores 0.837 at worst, wrong spots under 0.5


@cache
def _anchor(name: str) -> np.ndarray | None:
    return cv2.imread(str(ANCHORS_DIR / f"{name}.png"), cv2.IMREAD_GRAYSCALE)


def _match(screenshot: np.ndarray, name: str) -> tuple[int, int] | None:
    """Top-left of the reference image's best match, or None if missing or weak."""
    template = _anchor(name)
    if template is None:
        return None
    # Grey is about 3x faster than colour and finds the same spots.
    grey = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
    scores = cv2.matchTemplate(grey, template, cv2.TM_CCOEFF_NORMED)
    _, best, _, (x, y) = cv2.minMaxLoc(scores)
    return (x, y) if best >= MATCH_MIN else None


# The list starts 13 px below the header's top and is 156 px wide. It ends at
# the window's bottom border, a flat light-grey row. Shown filter buttons end
# in the same kind of row 60 px below the header; the list then starts under it.
BATTLE_TOP, BATTLE_W = 13, 156
FILTER_LINE = 60
GRIP_W = 12  # the resize grip in the bottom-left corner breaks the grey row


def find_battle_list(screenshot: np.ndarray) -> Rect | None:
    hit = _match(screenshot, "battle_list_header")
    return None if hit is None else _list_below(screenshot, hit)


def _list_below(screenshot: np.ndarray, header: Point) -> Rect | None:
    """The list box of the battle-list window whose header's top-left is *header*."""
    x, y = header
    rows = screenshot[y + BATTLE_TOP :, x + GRIP_W : x + BATTLE_W].astype(np.int16)
    bright = (rows.min(axis=(1, 2)) >= 100) & (np.ptp(rows, axis=(1, 2)) <= 30)
    lines = np.flatnonzero(bright) + BATTLE_TOP  # grey rows, from the header's top
    start = BATTLE_TOP
    if lines.size and abs(int(lines[0]) - FILTER_LINE) <= 1:
        start = int(lines[0]) + 1
        lines = lines[lines > start]
    if not lines.size:
        return None
    # The reader's box starts TOP_PADDING above the first creature row.
    return (x, y + start - TOP_PADDING, BATTLE_W, int(lines[0]) - start + TOP_PADDING)


# doc: docs/prs/player-list.md#players-window
PLAYERS_HEADER = "player_list_header"
SEARCH_EVERY = 5.0  # s between full-window searches while the Players window is lost
NEAR = 2  # px the header may sit off its last spot and still count as there


def find_player_list(screenshot: np.ndarray) -> Rect | None:
    hit = _match(screenshot, PLAYERS_HEADER)
    return None if hit is None else _list_below(screenshot, hit)


class PlayerListFollower:
    """The Players window's list box, re-found by its title when the window moves.

    The header is checked at its last spot every call (well under 1 ms); the
    full-window search (~45 ms) runs when it is no longer there, then once
    per ``SEARCH_EVERY`` s until found.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._header: Point | None = None
        self._next_search = float("-inf")

    def find(self, screenshot: np.ndarray) -> Rect | None:
        if self._header is not None:
            self._header = self._header_near(screenshot, self._header)
        if self._header is None and self._clock() >= self._next_search:
            self._header = _match(screenshot, PLAYERS_HEADER)
            if self._header is None:
                self._next_search = self._clock() + SEARCH_EVERY
        return None if self._header is None else _list_below(screenshot, self._header)

    @staticmethod
    def _header_near(screenshot: np.ndarray, at: Point) -> Point | None:
        template = _anchor(PLAYERS_HEADER)
        assert template is not None  # it was found with this image
        h, w = template.shape
        x0, y0 = max(0, at[0] - NEAR), max(0, at[1] - NEAR)
        spot = screenshot[y0 : at[1] + h + NEAR, x0 : at[0] + w + NEAR]
        if spot.shape[0] < h or spot.shape[1] < w:
            return None
        grey = cv2.cvtColor(spot, cv2.COLOR_BGR2GRAY)
        _, best, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(grey, template, cv2.TM_CCOEFF_NORMED))
        return (x0 + x, y0 + y) if best >= MATCH_MIN else None


# The reference image is the zoom-button strip right of the map, since the map
# itself changes as you walk.
MINIMAP_OFFSET, MINIMAP_SIZE = (-117, -50), (106, 109)


def find_minimap(screenshot: np.ndarray) -> Rect | None:
    hit = _match(screenshot, "minimap_frame")
    if hit is None:
        return None
    return (hit[0] + MINIMAP_OFFSET[0], hit[1] + MINIMAP_OFFSET[1], *MINIMAP_SIZE)


# The reference image is wider than the indicator so the static frame pins it.
# The ladder alone repeats and slipped 4 px on other floors.
FLOOR_OFFSET, FLOOR_SIZE = (2, 6), (22, 72)


def find_floor_indicator(screenshot: np.ndarray) -> Rect | None:
    hit = _match(screenshot, "floor_indicator")
    if hit is None:
        return None
    return (hit[0] + FLOOR_OFFSET[0], hit[1] + FLOOR_OFFSET[1], *FLOOR_SIZE)


# The game view has a flat dark-grey line along its top and left edges, and
# shows 15 x 11 square tiles. Text such as the lag overlay can cover parts of
# the left line, so the height comes from the width and the line only has to
# be mostly dark and end at the bottom corner.
GRID_TILES = (15, 11)
#: 15 tiles of 32 px, the client's own tile size. A half-screen client on a
#: 1920x1080 screen shows a 562 px wide game view (37.5 px tiles).
MIN_GRID_W = GRID_TILES[0] * 32


def find_game_grid(screenshot: np.ndarray) -> Rect | None:
    img = screenshot.astype(np.int16)
    dark = (np.ptp(img, axis=2) <= 3) & (img[..., 0] >= 14) & (img[..., 0] <= 34)
    rows = dark.shape[0]
    best: Rect | None = None
    for y in range(rows - 1):
        for x0, x1 in _runs(dark[y]):
            w = x1 - x0 - 1  # the line covers both corners
            if w < MIN_GRID_W or (best is not None and w <= best[2]):
                continue
            guess = round(w * GRID_TILES[1] / GRID_TILES[0])
            for h in (guess, guess - 1, guess + 1):
                end = y + h + 1  # bottom corner of the left line
                if end >= rows or not dark[end, x0] or (end + 1 < rows and dark[end + 1, x0]):
                    continue
                if dark[y : end + 1, x0].mean() >= 0.9:
                    best = (x0 + 1, y + 1, w, h)
                    break
    return best


# The status icons sit in the row left of the Stop button.
STATUS_OFFSET, STATUS_SIZE = (-118, 0), (108, 12)


def find_status_icons(screenshot: np.ndarray) -> Rect | None:
    hit = _match(screenshot, "stop_button")
    if hit is None:
        return None
    return (hit[0] + STATUS_OFFSET[0], hit[1] + STATUS_OFFSET[1], *STATUS_SIZE)


# doc: docs/prs/targeting.md#chase-mode
# Stand While Fighting and Chase Opponent sit stacked above the Stop button.
CHASE_OFFSET, CHASE_SIZE = (0, -144), (20, 43)


def find_chase_buttons(screenshot: np.ndarray) -> Rect | None:
    hit = _match(screenshot, "stop_button")
    if hit is None:
        return None
    return (hit[0] + CHASE_OFFSET[0], hit[1] + CHASE_OFFSET[1], *CHASE_SIZE)


# doc: docs/prs/gui-calibration.md#ui-scale
SCALES = np.arange(0.5, 3.0001, 0.05)


def find_ui_scale(screenshot: np.ndarray) -> tuple[float, Point]:
    """UI scale and the Stop button's top-left; (1.0, (0, 0)) when not found."""
    anchor = _anchor("stop_button")
    if anchor is None:
        return 1.0, (0, 0)
    grey = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
    h, w = anchor.shape
    best, scale, at = MATCH_MIN, 1.0, (0, 0)
    # ponytail: 51 full-frame matches (a few seconds), fine for a one-off calibration
    for s in sorted(SCALES, key=lambda s: abs(s - 1)):
        size = (round(w * s), round(h * s))
        if size[0] > grey.shape[1] or size[1] > grey.shape[0]:
            continue
        res = cv2.matchTemplate(grey, cv2.resize(anchor, size), cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score > best:
            best, scale, at = score, round(float(s), 2), (int(loc[0]), int(loc[1]))
        if best >= 0.99:
            break
    return scale, at


# doc: docs/prs/gui-calibration.md#layout-check
def find_stop_button(screenshot: np.ndarray, scale: float) -> Point | None:
    """The Stop button's top-left at UI *scale*; None if no anchor or no match."""
    anchor = _anchor("stop_button")
    if anchor is None:
        return None
    h, w = anchor.shape
    anchor = cv2.resize(anchor, (round(w * scale), round(h * scale)))
    grey = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
    if anchor.shape[0] > grey.shape[0] or anchor.shape[1] > grey.shape[1]:
        return None
    _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(grey, anchor, cv2.TM_CCOEFF_NORMED))
    return (int(loc[0]), int(loc[1])) if score >= MATCH_MIN else None


Finder = Callable[[np.ndarray], Rect | None]

FINDERS: list[tuple[str, Finder]] = [
    ("hp_bar", find_hp_bar),
    ("mp_bar", find_mp_bar),
    ("battle_list", find_battle_list),
    ("minimap", find_minimap),
    ("floor_indicator", find_floor_indicator),
    ("game_grid", find_game_grid),
    ("status_icons", find_status_icons),
    ("chase_buttons", find_chase_buttons),
    ("player_list", find_player_list),
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
