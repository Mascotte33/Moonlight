"""Shortest walk over the client's walk-cost map, for arrow-key walking.

``data/map/cost/{x}_{y}_{z}.png`` are renamed copies of the client's
``Minimap_WaypointCost`` tiles: 256x256 px, 1 px = 1 sqm. Yellow pixels
(BGR 0, 255, 255) are blocked or unexplored; every other pixel is walkable.
A missing tile counts as blocked.

:func:`next_step` runs a 4-way breadth-first search in a window around the
player and the goal and returns the first step and the path length. Arrow keys walk
only in 4 directions, so diagonals are not searched.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

__all__ = ["CostMap", "next_step"]

_TILE = 256
_NAME = re.compile(r"^(\d+)_(\d+)_(\d+)$")
_YELLOW = (0, 255, 255)
_STEPS = ((0, -1), (0, 1), (-1, 0), (1, 0))


class CostMap:
    """Walkable tiles of each floor, loaded on first use."""

    def __init__(self, directory: Path) -> None:
        self._paths: dict[tuple[int, int, int], Path] = {}
        for path in sorted(directory.glob("*.png")) if directory.is_dir() else []:
            m = _NAME.match(path.stem)
            if m:
                x, y, z = (int(v) for v in m.groups())
                self._paths[(x, y, z)] = path
        self._tiles: dict[tuple[int, int, int], np.ndarray | None] = {}

    @property
    def empty(self) -> bool:
        """True when the directory held no cost tiles."""
        return not self._paths

    def walkable(self, x: int, y: int, z: int) -> bool:
        key = (x - x % _TILE, y - y % _TILE, z)
        if key not in self._tiles:
            path = self._paths.get(key)
            image = cv2.imread(str(path), cv2.IMREAD_COLOR) if path else None
            self._tiles[key] = None if image is None else np.any(image != _YELLOW, axis=2)
        tile = self._tiles[key]
        return tile is not None and bool(tile[y % _TILE, x % _TILE])


def next_step(
    start: tuple[int, int, int],
    goal: tuple[int, int, int],
    walkable: Callable[[int, int, int], bool],
    *,
    margin: int = 30,
) -> tuple[tuple[int, int], int] | None:
    """``((dx, dy), length)`` of a shortest path on *start*'s floor, or ``None``.

    The goal tile counts as walkable (a waypoint may sit on an unexplored
    tile). ``None`` when there is no path inside the search window.
    """
    (sx, sy, z), (gx, gy, _) = start, goal
    if (sx, sy) == (gx, gy):
        return None
    m = margin
    x0, x1 = min(sx, gx) - m, max(sx, gx) + m
    y0, y1 = min(sy, gy) - m, max(sy, gy) + m
    # ponytail: BFS ignores the gray walk costs; Dijkstra on them if
    # routes cut through slow ground.
    first: dict[tuple[int, int], tuple[int, int] | None] = {(sx, sy): None}
    queue = deque([(sx, sy, 0)])
    while queue:
        x, y, n = queue.popleft()
        for dx, dy in _STEPS:
            nx, ny = x + dx, y + dy
            if (nx, ny) in first or not (x0 <= nx <= x1 and y0 <= ny <= y1):
                continue
            if (nx, ny) != (gx, gy) and not walkable(nx, ny, z):
                continue
            step = first[(x, y)] or (dx, dy)
            if (nx, ny) == (gx, gy):
                return step, n + 1
            first[(nx, ny)] = step
            queue.append((nx, ny, n + 1))
    return None
