"""The game map with a route drawn on it, for the Map tab and ``tools/map.py``.

The client's colour minimap tiles (``data/maps/tibia/{x}_{y}_{z}.png``,
256x256 px, 1 px = 1 sqm) are cut into the shown area and scaled up. Each
positional waypoint is a numbered dot coloured by its kind; waypoints in a
row on one floor are joined by the path the bot walks between them (the walk
map's cheapest path, ``next_step``), or a straight red line when it finds none
(a failing leg). A waypoint whose next one is on another floor
gets a triangle pointing up or down.

# doc: docs/prs/map.md#map-view
# doc: docs/prs/map.md#leg-check
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import cv2
import numpy as np

from moonlight.cavebot.cavebot import Waypoint, has_tile
from moonlight.cavebot.pathing import next_step

__all__ = [
    "PICTURE_MIN_SCALE", "SCALES", "MapTiles", "MapView", "bad_legs", "draw_route", "leg_path",
    "legs",
    "waypoint_at",
]  # fmt: skip

_TILE = 256
#: Pixels per sqm the view zooms through.
SCALES = (1, 2, 3, 4, 6, 8, 12, 16)
#: Dot colour (RGB) per waypoint kind; others are ``_OTHER``.
KIND_COLOURS = {
    "walk": (231, 237, 242),
    "node": (124, 129, 134),
    "stand": (201, 155, 62),
    "lure": (201, 155, 62),
    "rope": (107, 164, 248),
    "ladder": (107, 164, 248),
    "shovel": (107, 164, 248),
    "stairs": (107, 164, 248),
    "step": (115, 201, 165),
}
_OTHER = (190, 120, 230)
_LINE = (231, 237, 242)
_BAD = (229, 93, 81)
_ME = (115, 201, 165)
_SELECTED = (255, 255, 0)
_MONSTER = (240, 120, 40)
#: Monster pictures show from this zoom (px per sqm); farther out they cover the map.
PICTURE_MIN_SCALE = 4

Spot = tuple[int, int, int]


class MapTiles:
    """The colour minimap tiles of one map folder, loaded on first use."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        # ponytail: every tile seen stays loaded (~200 KB each); add an LRU if
        # panning the whole world ever fills memory.
        self._tiles: dict[Spot, np.ndarray | None] = {}

    def area(self, x0: int, y0: int, z: int, width: int, height: int) -> np.ndarray:
        """The RGB map of sqm ``x0..x0+width``, ``y0..y0+height``; unknown sqm black."""
        out = np.zeros((height, width, 3), np.uint8)
        for tx in range(x0 - x0 % _TILE, x0 + width, _TILE):
            for ty in range(y0 - y0 % _TILE, y0 + height, _TILE):
                tile = self._tile(tx, ty, z)
                if tile is None:
                    continue
                ax0, ax1 = max(x0, tx), min(x0 + width, tx + _TILE)
                ay0, ay1 = max(y0, ty), min(y0 + height, ty + _TILE)
                out[ay0 - y0 : ay1 - y0, ax0 - x0 : ax1 - x0] = tile[
                    ay0 - ty : ay1 - ty, ax0 - tx : ax1 - tx
                ]
        return out

    def _tile(self, x: int, y: int, z: int) -> np.ndarray | None:
        if (x, y, z) not in self._tiles:
            image = cv2.imread(str(self._directory / f"{x}_{y}_{z}.png"), cv2.IMREAD_COLOR)
            self._tiles[x, y, z] = None if image is None else cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        return self._tiles[x, y, z]


def legs(waypoints: list[Waypoint]) -> list[tuple[int, int]]:
    """Index pairs of positional waypoints in a row, last back to first (routes loop).

    A ``goto`` between two of them, or a ``boat`` as the first, is a jump, not a
    walk: no leg (docs/prs/boat.md#route-check).
    """
    spots = [i for i, wp in enumerate(waypoints) if has_tile(wp)]
    pairs: list[tuple[int, int]] = []
    prev: int | None = None
    for i, wp in enumerate(waypoints):
        if wp.action == "goto":
            prev = None
        elif has_tile(wp):
            if prev is not None:
                pairs.append((prev, i))
            prev = None if wp.action == "boat" else i
    if len(spots) > 2 and prev is not None:
        pairs.append((spots[-1], spots[0]))
    return pairs


#: ``path(a, b)``: the (x, y) sqm the bot walks from *a* (not included) to *b*
#: on one floor, or None when the walk map has no path.
LegPath = Callable[[Spot, Spot], list[tuple[int, int]] | None]


def leg_path(walkable: Callable[[int, int, int], bool]) -> LegPath:
    """The bot's walking path between two sqm (``next_step``'s tiles); cached."""

    @cache
    def path(a: Spot, b: Spot) -> list[tuple[int, int]] | None:
        if a[:2] == b[:2]:
            return []
        found = next_step(a, b, walkable)
        return None if found is None else found[2]

    return path


def bad_legs(waypoints: list[Waypoint], path: LegPath) -> list[tuple[int, int]]:
    """The legs on one floor the walk map finds no path for."""
    bad = []
    for i, j in legs(waypoints):
        a, b = waypoints[i], waypoints[j]
        if a.z == b.z and path((a.x, a.y, a.z), (b.x, b.y, b.z)) is None:
            bad.append((i, j))
    return bad


def waypoint_at(waypoints: list[Waypoint], x: int, y: int, z: int) -> int | None:
    """The last positional waypoint on sqm (x, y, z), or None."""
    for i in range(len(waypoints) - 1, -1, -1):
        wp = waypoints[i]
        if has_tile(wp) and (wp.x, wp.y, wp.z) == (x, y, z):
            return i
    return None


def draw_route(
    area: np.ndarray,
    origin: tuple[int, int],
    z: int,
    scale: int,
    waypoints: list[Waypoint],
    bad: list[tuple[int, int]],
    *,
    selected: int | None = None,
    me: Spot | None = None,
    path: LegPath | None = None,
    monsters: Sequence[tuple[int, int, np.ndarray | None]] = (),
) -> np.ndarray:
    """*area* (``MapTiles.area`` at *origin*) scaled *scale* times with the route on floor *z*.

    With *path*, a leg that has one is drawn along it; else legs are straight.
    *monsters* are spawn sqm (x, y, RGBA picture or None for a dot) on floor *z*,
    drawn under the route.
    """
    image = cv2.resize(area, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    x0, y0 = origin
    half = scale // 2

    def px(x: int, y: int) -> tuple[int, int]:
        return (x - x0) * scale + half, (y - y0) * scale + half

    for x, y, rgba in monsters:
        if rgba is None:
            cv2.circle(image, px(x, y), max(1, half // 2 + 1), _MONSTER, -1)
        else:
            _paste(image, rgba, *px(x, y))

    for i, j in legs(waypoints):
        a, b = waypoints[i], waypoints[j]
        if a.z != z or b.z != z:
            continue
        if (i, j) in bad:
            cv2.line(image, px(a.x, a.y), px(b.x, b.y), _BAD, 3)
            continue
        tiles = path((a.x, a.y, a.z), (b.x, b.y, b.z)) if path is not None else None
        points = [(a.x, a.y), *(tiles if tiles is not None else [(b.x, b.y)])]
        line = np.array([px(*p) for p in points], np.int32)
        cv2.polylines(image, [line], False, _LINE, 1)
    radius = max(3, half)
    for i, j in legs(waypoints):
        a, b = waypoints[i], waypoints[j]
        if a.z == z != b.z:  # a floor change: triangle up (lower z) or down
            cx, cy = px(a.x, a.y)
            r = radius + 4
            tip = -r if b.z < a.z else r
            triangle = np.array([(cx, cy + tip), (cx - r, cy - tip), (cx + r, cy - tip)], np.int32)
            cv2.polylines(image, [triangle], True, KIND_COLOURS["rope"], 2)
    for i, wp in enumerate(waypoints):
        if not has_tile(wp) or wp.z != z:
            continue
        centre = px(wp.x, wp.y)
        cv2.circle(image, centre, radius, KIND_COLOURS.get(wp.action, _OTHER), -1)
        if i == selected:
            cv2.circle(image, centre, radius + 3, _SELECTED, 2)
        if scale >= 4:
            at = (centre[0] + radius + 1, centre[1] - radius)
            cv2.putText(image, str(i + 1), at, cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 3)
            cv2.putText(image, str(i + 1), at, cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    if me is not None and me[2] == z:
        cx, cy = px(me[0], me[1])
        r = radius + 2
        cv2.line(image, (cx - r, cy - r), (cx + r, cy + r), _ME, 2)
        cv2.line(image, (cx - r, cy + r), (cx + r, cy - r), _ME, 2)
    return image


@dataclass
class MapView:
    """What the Map tab shows: the sqm in the middle, the floor and the zoom."""

    x: int
    y: int
    z: int
    scale: int = 4
    width: int = 800  # pixels
    height: int = 600

    @property
    def origin(self) -> tuple[int, int]:
        """The sqm at the top-left pixel."""
        return self.x - self.width // (2 * self.scale), self.y - self.height // (2 * self.scale)

    @property
    def size(self) -> tuple[int, int]:
        """Sqm across and down (one more, so the edge is covered)."""
        return self.width // self.scale + 1, self.height // self.scale + 1

    def sqm_at(self, px: int, py: int) -> tuple[int, int]:
        """The sqm under pixel (px, py)."""
        x0, y0 = self.origin
        return x0 + px // self.scale, y0 + py // self.scale

    def pan(self, dx_px: int, dy_px: int) -> None:
        """Move the map *dx_px*, *dy_px* pixels with the mouse (the sqm under it stays)."""
        self.x -= round(dx_px / self.scale)
        self.y -= round(dy_px / self.scale)

    def zoom(self, steps: int, px: int, py: int) -> None:
        """Zoom *steps* scales in (+) or out (-), keeping the sqm under (px, py) there."""
        index = SCALES.index(self.scale) + steps
        scale = SCALES[min(max(index, 0), len(SCALES) - 1)]
        sx, sy = self.sqm_at(px, py)
        self.scale = scale
        nx, ny = self.sqm_at(px, py)
        self.x += sx - nx
        self.y += sy - ny

    def render(
        self,
        tiles: MapTiles,
        waypoints: list[Waypoint],
        bad: list[tuple[int, int]],
        *,
        selected: int | None = None,
        me: Spot | None = None,
        path: LegPath | None = None,
        monsters: dict[Spot, list[str]] | None = None,
        pictures: dict[str, np.ndarray] | None = None,
    ) -> np.ndarray:
        """The view as an RGB image ``height`` x ``width``; *monsters*: spawn sqm to show,
        as the monster's RGBA picture from *pictures* from ``PICTURE_MIN_SCALE`` up."""
        x0, y0 = self.origin
        w, h = self.size
        area = tiles.area(x0, y0, self.z, w, h)
        size = min(32, 2 * self.scale)  # a monster covers 2 sqm across at 16 px per sqm
        resized: dict[str, np.ndarray | None] = {}

        def picture(names: list[str]) -> np.ndarray | None:
            if self.scale < PICTURE_MIN_SCALE or not pictures:
                return None
            name = names[0]
            if name not in resized:
                rgba = pictures.get(name)
                resized[name] = None if rgba is None else _fit(rgba, size)
            return resized[name]

        shown = [(x, y, picture(names)) for (x, y, z), names in (monsters or {}).items()
                 if z == self.z and x0 <= x < x0 + w and y0 <= y < y0 + h]  # fmt: skip
        image = draw_route(area, (x0, y0), self.z, self.scale, waypoints, bad,
                           selected=selected, me=me, path=path, monsters=shown)  # fmt: skip
        return image[: self.height, : self.width]


def _fit(rgba: np.ndarray, size: int) -> np.ndarray:
    """*rgba* shrunk so its longer side is *size* px."""
    scale = size / max(rgba.shape[:2])
    if scale >= 1:
        return rgba
    return cv2.resize(rgba, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


# doc: docs/prs/route-tools.md#monster-pictures
def _paste(image: np.ndarray, rgba: np.ndarray, cx: int, cy: int) -> None:
    """Blend *rgba* onto *image* centred on pixel (cx, cy), cut at the edges."""
    ph, pw = rgba.shape[:2]
    left, top = cx - pw // 2, cy - ph // 2
    ix0, iy0 = max(left, 0), max(top, 0)
    ix1, iy1 = min(left + pw, image.shape[1]), min(top + ph, image.shape[0])
    if ix0 >= ix1 or iy0 >= iy1:
        return
    part = rgba[iy0 - top : iy1 - top, ix0 - left : ix1 - left].astype(np.float32)
    alpha = part[:, :, 3:] / 255
    under = image[iy0:iy1, ix0:ix1].astype(np.float32)
    image[iy0:iy1, ix0:ix1] = (part[:, :, :3] * alpha + under * (1 - alpha)).astype(np.uint8)
