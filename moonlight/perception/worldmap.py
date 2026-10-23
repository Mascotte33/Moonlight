"""Absolute position by matching the radar against the client's map tiles.

``data/map/{x}_{y}_{z}.png`` are copies of the client's minimap tiles:
256x256 px, 1 px = 1 sqm, named by the world coordinate of their top-left
corner. The radar draws ``zoom`` px per sqm (the player picks it, so each of
``zooms`` is tried), so the crop is downscaled by averaging ``zoom`` x
``zoom`` blocks (each pixel phase is tried, best wins) and then located on
the stitched floor with ``TM_SQDIFF_NORMED``.

A search near the last position (+-``near_sqm``) costs well under 1 ms. A
whole-floor search first matches a 4x smaller copy of the floor, then
refines near the best spot; it runs at most every ``full_search_every``
seconds.
"""

from __future__ import annotations

import itertools
import re
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

__all__ = ["WorldMap", "MATCH_MAX_SCORE"]

#: Good matches score ~0.004-0.03 on the golden frames, wrong ones >= 0.4.
MATCH_MAX_SCORE = 0.08

_TILE = 256
_COARSE = 4
_NAME = re.compile(r"^(\d+)_(\d+)_(\d+)$")


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
        self._near = int(near_sqm)
        self._every = float(full_search_every)
        self._max_score = float(max_score)
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
        h, w = radar_rgb.shape[:2]
        best: tuple[float, int, int, int] | None = None
        for z_ in self._zooms:
            spot = near or self._coarse_spot(radar_rgb, z, image, x0, y0, z_)
            # Tile edges may fall on any pixel phase; averaging each zoom block
            # blurs a wrong phase, so the right one scores best.
            for px, py in itertools.product(range(z_), repeat=2):
                th, tw = (h - py) // z_, (w - px) // z_
                block = radar_rgb[py : py + th * z_, px : px + tw * z_, :3]
                small = cv2.resize(block, (tw, th), interpolation=cv2.INTER_AREA)
                ox = max(0, spot[0] - x0 - tw // 2 - self._near)
                oy = max(0, spot[1] - y0 - th // 2 - self._near)
                area = image[oy : oy + th + 2 * self._near, ox : ox + tw + 2 * self._near]
                if area.shape[0] < th or area.shape[1] < tw:
                    continue
                res = cv2.matchTemplate(area, small, cv2.TM_SQDIFF_NORMED)
                score, _, loc, _ = cv2.minMaxLoc(res)
                x = x0 + ox + loc[0] + (w // 2 - px) // z_
                y = y0 + oy + loc[1] + (h // 2 - py) // z_
                if best is None or score < best[0]:
                    best = (float(score), x, y, z_)
        if best is None or not best[0] <= self._max_score:
            return None
        self.zoom = best[3]
        return best[1], best[2]

    def _coarse_spot(
        self, radar_rgb: np.ndarray, z: int, image: np.ndarray, x0: int, y0: int, zoom: int
    ) -> tuple[int, int]:
        """Rough world ``(x, y)`` of the radar center from a 1/4 scale match."""
        if z not in self._coarse:
            h, w = image.shape[:2]
            size = (w // _COARSE, h // _COARSE)
            self._coarse[z] = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        h, w = radar_rgb.shape[:2]
        f = zoom * _COARSE
        small = cv2.resize(radar_rgb[:, :, :3], (w // f, h // f), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(self._coarse[z], small, cv2.TM_SQDIFF_NORMED)
        _, _, loc, _ = cv2.minMaxLoc(res)
        return x0 + (loc[0] * f + w // 2) // zoom, y0 + (loc[1] * f + h // 2) // zoom

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
            self._floors[z] = (image, x0, y0)
        return self._floors[z]
