"""Absolute position by matching the radar against the client's map tiles.

``data/maps/<map>/{x}_{y}_{z}.png`` are copies of the client's minimap tiles:
256x256 px, 1 px = 1 sqm, named by the world coordinate of their top-left
corner. The radar draws ``zoom`` px per sqm (the player picks it, so each of
``zooms`` is tried), so the crop is downscaled by averaging ``zoom`` x
``zoom`` blocks (each pixel phase is tried, best wins) and then located on
the stitched floor with ``TM_SQDIFF_NORMED``.

A search near the last position (+-``near_sqm``) costs well under 1 ms. A
whole-floor search first matches a 4x smaller copy of the floor, then
refines near the best spot; it runs at most every ``full_search_every``
seconds.

Only the radar's lit pixels and a thin black outline around them are
compared: the white player cross and black parts farther out (unexplored, or
not drawn on the radar) are masked out, so a sparse radar is scored on what it
shows. # doc: docs/prs/client-hotkeys.md#lit-pixels-only
"""

from __future__ import annotations

import itertools
import re
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

__all__ = ["DISTINCT_RATIO", "WorldMap", "MATCH_MAX_SCORE", "NEAR_MATCH_MAX_SCORE"]

#: Good matches score ~0.004-0.03 on the golden frames, wrong ones >= 0.4.
#: The Thais sewer with map marks on the radar scored 0.06-0.08 (2026-11-01),
#: so 0.08 dropped good matches there.
# doc: docs/prs/position-lost.md#wider-match
MATCH_MAX_SCORE = 0.15
#: A search near the last position may score up to this. A mostly black
#: radar (Ab'Dendriel floor 4, 2026-11-05) scored 0.207 at the right spot;
#: wrong whole-floor matches on the golden frames scored from 0.215, but a
#: +-20 sqm window leaves little to match wrongly.
# doc: docs/prs/client-hotkeys.md#sparse-radar
NEAR_MATCH_MAX_SCORE = 0.25
#: A whole-floor search may score up to ``NEAR_MATCH_MAX_SCORE`` too when no
#: other spot on the floor (1/4 scale) comes within this factor of the best.
#: The live floor-4 radar: 2.5x; the golden frames' wrong 0.15-0.25 matches:
#: 1.01-1.02x (2026-11-05).
# doc: docs/prs/client-hotkeys.md#distinct-full-match
DISTINCT_RATIO = 2.0

_TILE = 256
_COARSE = 4
#: Black radar px this close to a lit one are compared (farther ones may be
#: map parts the radar does not draw).
_BORDER = 3
_NAME = re.compile(r"^(\d+)_(\d+)_(\d+)$")


#: The minimap's rock grey. The client draws it over every explored tile
#: with nothing else on it, the stored map only on rock, so a radar with a
#: grey block where the map is black scored 0.30 at its spot (Venore tainted
#: caves floor 11, 2026-11-07). Both count it as black.
_ROCK = (102, 102, 102)


# doc: docs/prs/near-targeting.md#rock-is-unexplored
def _rock_as_black(rgb: np.ndarray) -> np.ndarray:
    """A copy of *rgb* with the rock grey blacked out."""
    out = rgb[:, :, :3].copy()
    out[(out == _ROCK).all(axis=2)] = 0
    return out


#: Every map colour has each channel a multiple of this (the client's 216-colour palette).
_PALETTE_STEP = 51


# doc: docs/prs/position-tracking.md#mask-marks
def _off_palette(radar_rgb: np.ndarray) -> np.ndarray:
    """True on the map marks: pixels off the map palette, and 1 px around them."""
    off = (radar_rgb[:, :, :3] % _PALETTE_STEP != 0).any(axis=2).astype(np.uint8)
    return cv2.dilate(off, np.ones((3, 3), np.uint8)) > 0


def _lit(radar_rgb: np.ndarray) -> np.ndarray:
    """1.0 on the radar pixels to compare, else 0.0: the lit ones and the black
    ones within ``_BORDER`` px of them (the shapes' outline), never the white
    player cross or a map mark."""
    total = radar_rgb[:, :, :3].astype(np.int32).sum(axis=2)
    lit = (total > 30).astype(np.uint8)
    near_lit = cv2.dilate(lit, np.ones((2 * _BORDER + 1,) * 2, np.uint8))
    compare: np.ndarray = (
        (near_lit > 0) & (total < 700) & ~_off_palette(radar_rgb)
    ).astype(np.float32)
    return compare


#: Share of the radar's colour edges on one pixel phase that makes it zoomed:
#: zoom-2 radars 0.93-0.98, zoom-1 ones ~0.5 (logs, 2026-11-07).
_ZOOMED_EDGE_SHARE = 0.75
#: Fewer edges than this on either axis say nothing about the zoom (live radars: 430-1900).
_ZOOM_MIN_EDGES = 200


# doc: docs/prs/near-targeting.md#radar-zoom
def _radar_zooms(radar_rgb: np.ndarray, zooms: tuple[int, ...]) -> tuple[int, ...]:
    """The zoom the radar is drawn at, read from its edges; all *zooms* with too few edges.

    At *zoom* px per tile every colour edge falls between tiles, so on one
    pixel phase in *zoom*. Matching at the wrong zoom can win: a cave radar
    with map marks scored 0.20 at its spot (zoom 1) and 0.09 on a grey area
    735 sqm away at zoom 2 (Venore tainted caves, 2026-11-07).
    """
    total = radar_rgb[:, :, :3].astype(np.int32).sum(axis=2)
    edges_per_column = (np.abs(np.diff(total, axis=1)) > 30).sum(axis=0)
    edges_per_row = (np.abs(np.diff(total, axis=0)) > 30).sum(axis=1)
    if min(edges_per_column.sum(), edges_per_row.sum()) < _ZOOM_MIN_EDGES:
        return zooms
    for zoom in sorted(zooms, reverse=True):
        on_one_phase = all(
            max(edges[phase::zoom].sum() for phase in range(zoom))
            >= _ZOOMED_EDGE_SHARE * edges.sum()
            for edges in (edges_per_column, edges_per_row)
        )
        if zoom == 1 or on_one_phase:
            return (zoom,)
    return zooms


def _shrink(
    rgb: np.ndarray, lit: np.ndarray, size: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray]:
    """*rgb* averaged down to *size*, and the mask of blocks mostly to compare."""
    small = cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)
    return small, (cv2.resize(lit, size, interpolation=cv2.INTER_AREA) > 0.5).astype(np.uint8)


def _match_lit(area: np.ndarray, radar_rgb: np.ndarray, lit: np.ndarray) -> np.ndarray:
    """``TM_SQDIFF_NORMED`` of *radar_rgb* on *area* over the *lit* mask only;
    spots with nothing to compare are inf."""
    if not lit.any():
        return np.full((area.shape[0] - lit.shape[0] + 1, area.shape[1] - lit.shape[1] + 1), np.inf)
    mask = np.repeat(lit[:, :, None], 3, axis=2)
    res = cv2.matchTemplate(area, radar_rgb, cv2.TM_SQDIFF_NORMED, mask=mask)
    res[~np.isfinite(res)] = np.inf
    return res


class WorldMap:
    """Locate a radar crop on the stitched map of one floor."""

    def __init__(
        self,
        directory: Path,
        *,
        zooms: tuple[int, ...] = (1, 2),
        near_sqm: int = 20,
        full_search_every: float = 3.0,
        max_score: float = MATCH_MAX_SCORE,
        near_max_score: float = NEAR_MATCH_MAX_SCORE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tiles: dict[int, list[tuple[int, int, Path]]] = {}
        for path in sorted(directory.glob("*.png")) if directory.is_dir() else []:
            m = _NAME.match(path.stem)
            if m:
                x, y, z = (int(v) for v in m.groups())
                self._tiles.setdefault(z, []).append((x, y, path))
        self._floors: dict[int, tuple[np.ndarray, int, int]] = {}
        self._coarse: dict[int, np.ndarray] = {}
        self._zooms = tuple(max(1, int(z)) for z in zooms)
        #: Radar px per sqm of the last match; the user can change the minimap zoom.
        self.zoom = self._zooms[-1]
        #: Score of the last match (0 is a perfect fit).
        self.score = 0.0
        self._near = int(near_sqm)
        self._every = float(full_search_every)
        self._max_score = float(max_score)
        self._near_max_score = float(near_max_score)
        self._clock = clock
        self._last_full: float | None = None

    @property
    def empty(self) -> bool:
        """True when the directory held no map tiles."""
        return not self._tiles

    def match(
        self, radar_rgb: np.ndarray, z: int, near: tuple[int, int] | None = None
    ) -> tuple[int, int] | None:
        """World ``(x, y)`` of the radar center on floor *z*, or ``None``.

        With *near*, only a window around it is searched. Without it, the
        whole floor is searched unless one ran less than
        ``full_search_every`` seconds ago.
        """
        floor = self._floor(z)
        if floor is None:
            return None
        image, x0, y0 = floor
        if near is None:
            now = self._clock()
            if self._last_full is not None and now - self._last_full < self._every:
                return None
            self._last_full = now
        radar_rgb = _rock_as_black(radar_rgb)
        h, w = radar_rgb.shape[:2]
        lit = _lit(radar_rgb)
        best: tuple[float, int, int, int, bool] | None = None
        for z_ in _radar_zooms(radar_rgb, self._zooms):
            distinct = False
            if near is None:
                spot, distinct = self._coarse_spot(radar_rgb, lit, z, image, x0, y0, z_)
            else:
                spot = near
            # Tile edges may fall on any pixel phase; averaging each zoom block
            # blurs a wrong phase, so the right one scores best.
            for px, py in itertools.product(range(z_), repeat=2):
                th, tw = (h - py) // z_, (w - px) // z_
                rows, cols = slice(py, py + th * z_), slice(px, px + tw * z_)
                small, small_lit = _shrink(radar_rgb[rows, cols, :3], lit[rows, cols], (tw, th))
                ox = max(0, spot[0] - x0 - tw // 2 - self._near)
                oy = max(0, spot[1] - y0 - th // 2 - self._near)
                area = image[oy : oy + th + 2 * self._near, ox : ox + tw + 2 * self._near]
                if area.shape[0] < th or area.shape[1] < tw:
                    continue
                res = _match_lit(area, small, small_lit)
                score, _, loc, _ = cv2.minMaxLoc(res)
                x = x0 + ox + loc[0] + (w // 2 - px) // z_
                y = y0 + oy + loc[1] + (h // 2 - py) // z_
                if best is None or score < best[0]:
                    best = (float(score), x, y, z_, distinct)
        loose = near is not None or (best is not None and best[4])
        limit = self._near_max_score if loose else self._max_score
        if best is None or not best[0] <= limit:
            return None
        self.zoom, self.score = best[3], best[0]
        return best[1], best[2]

    def _coarse_spot(
        self,
        radar_rgb: np.ndarray,
        lit: np.ndarray,
        z: int,
        image: np.ndarray,
        x0: int,
        y0: int,
        zoom: int,
    ) -> tuple[tuple[int, int], bool]:
        """Rough world ``(x, y)`` of the radar center from a 1/4 scale match,
        and whether no spot away from it scores within ``DISTINCT_RATIO``."""
        if z not in self._coarse:
            h, w = image.shape[:2]
            size = (w // _COARSE, h // _COARSE)
            self._coarse[z] = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        h, w = radar_rgb.shape[:2]
        f = zoom * _COARSE
        small, small_lit = _shrink(radar_rgb[:, :, :3], lit, (w // f, h // f))
        res = _match_lit(self._coarse[z], small, small_lit)
        score, _, loc, _ = cv2.minMaxLoc(res)
        bx, by = loc
        res[max(0, by - 8) : by + 9, max(0, bx - 8) : bx + 9] = np.inf  # the best spot's area
        distinct = bool(res.min() >= DISTINCT_RATIO * score)
        return (x0 + (bx * f + w // 2) // zoom, y0 + (by * f + h // 2) // zoom), distinct

    def _floor(self, z: int) -> tuple[np.ndarray, int, int] | None:
        """The stitched RGB map of floor *z* and its world origin (cached)."""
        if z not in self._floors:
            tiles = self._tiles.get(z)
            if not tiles:
                return None
            x0 = min(t[0] for t in tiles)
            y0 = min(t[1] for t in tiles)
            x1 = max(t[0] for t in tiles) + _TILE
            y1 = max(t[1] for t in tiles) + _TILE
            image = np.zeros((y1 - y0, x1 - x0, 3), np.uint8)
            for x, y, path in tiles:
                tile = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if tile is not None:
                    image[y - y0 : y - y0 + _TILE, x - x0 : x - x0 + _TILE] = cv2.cvtColor(
                        tile[:_TILE, :_TILE], cv2.COLOR_BGR2RGB
                    )
            self._floors[z] = (_rock_as_black(image), x0, y0)
        return self._floors[z]
