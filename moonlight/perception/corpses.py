"""Lootable corpses on the game grid, found by their turning loot ring.

The OTS marks a corpse with loot by a ring of small white and light-blue
dots that turns and sparkles. One frame cannot tell those dots from light
stone or gravel, which look the same; but the ring moves and the floor does
not. :class:`RingWatch` compares the game grid with how it looked ``GAP``
seconds before and counts the bright dots that are new, tile by tile. While
the view scrolls (the player walks) the older look is first shifted by the
scroll (found by phase correlation), so a ring passed on the way is seen too.

# doc: docs/prs/corpse-loot.md#ring-check
# doc: docs/prs/client-hotkeys.md#ring-anywhere
"""

from __future__ import annotations

from typing import NamedTuple

import cv2
import numpy as np

from moonlight.perception.creatures import PLAYER_SLOT

__all__ = ["FARTHEST", "GAP", "MOVED", "RING_PIXELS", "Ring", "RingWatch"]

_COLS, _ROWS = 15, 11
#: Most tiles from the player to the grid's edge (its columns are 7 to each side).
FARTHEST = 7
#: Seconds between the two looks; the ring turns visibly in that time.
GAP = 0.1
#: New bright pixels that mean a ring (a real ring gave 41-128, still floor 0).
RING_PIXELS = 25
#: Mean color change of the block above which the view moved (still <= 2.4, walking >= 10).
MOVED = 6.0


def _bright(block: np.ndarray) -> np.ndarray:
    """White or light-blue pixels: all channels bright, blue the strongest."""
    b, g, r = block[..., 0], block[..., 1], block[..., 2]
    out: np.ndarray = (np.minimum(np.minimum(r, g), b) >= 150) & (b >= g) & (b >= r)
    return out


def _overlap(n: int, d: int) -> tuple[slice, slice]:
    """Slices of the new and the old look that show the same floor after a *d* px scroll."""
    d = max(-n, min(n, d))  # a scroll past the block leaves no overlap
    return (slice(d, n), slice(0, n - d)) if d >= 0 else (slice(0, n + d), slice(-d, n))


def _unscrolled(
    block: np.ndarray, ref: np.ndarray, player: tuple[slice, slice]
) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """*block* and *ref* cut to the floor both show, with the scroll undone.

    *player* is the player's tile in *block*. Also returns where the cut
    starts in *block* (x, y).
    """
    old, new = (a.mean(axis=2).astype(np.float32) for a in (ref, block))
    (dx, dy), _ = cv2.phaseCorrelate(old, new)
    h, w = block.shape[:2]
    (by, ry), (bx, rx) = _overlap(h, round(dy)), _overlap(w, round(dx))
    block, ref = block[by, bx].copy(), ref[ry, rx].copy()
    # the player walks along with the view: blank their own tile, its sprite steps
    pys, pxs = player
    py = slice(max(pys.start - by.start, 0), max(pys.stop - by.start, 0))
    px = slice(max(pxs.start - bx.start, 0), max(pxs.stop - bx.start, 0))
    block[py, px] = ref[py, px] = 0
    return block, ref, (bx.start, by.start)


class Ring(NamedTuple):
    """A turning loot ring: the middle of its tile and how far it is."""

    point: tuple[int, int]  # frame pixel
    tiles_away: int  # 0 = the player's tile, 1 = the 8 around it


class RingWatch:
    """Finds loot rings anywhere on the game grid."""

    def __init__(self, grid: tuple[int, int, int, int]) -> None:
        gx, gy, gw, gh = grid
        tw, th = gw / _COLS, gh / _ROWS
        col, row = PLAYER_SLOT
        self._origin, self._tile_size = (gx, gy), (tw, th)
        self._box = (slice(gy, gy + gh), slice(gx, gx + gw))
        self._player = (
            slice(round(row * th), round((row + 1) * th)),
            slice(round(col * tw), round((col + 1) * tw)),
        )
        self._ref: np.ndarray | None = None
        self._ref_at = 0.0

    def rings(self, frame: np.ndarray, now: float) -> list[Ring]:
        """Rings that turned since the last look, nearest first (BGR *frame*)."""
        if self._ref is not None and now - self._ref_at < GAP:
            return []
        block = frame[self._box].astype(np.int16)
        ref, self._ref, self._ref_at = self._ref, block, now
        if ref is None or ref.shape != block.shape:
            return []
        cut = (0, 0)
        if float(np.abs(block - ref).mean()) > MOVED:
            block, ref, cut = _unscrolled(block, ref, self._player)  # the view scrolled
            if block.size == 0 or float(np.abs(block - ref).mean()) > MOVED:
                return []  # scrolled too far, or not a plain scroll
        # ponytail: a white spell flash or an animated light-blue field counts
        # too; it costs a loot click on it.
        ys, xs = np.nonzero(_bright(block) & ~_bright(ref))
        if len(xs) < RING_PIXELS:
            return []
        (tw, th), (gx, gy) = self._tile_size, self._origin
        cols = np.clip((xs + cut[0]) // tw, 0, _COLS - 1).astype(int)
        rows = np.clip((ys + cut[1]) // th, 0, _ROWS - 1).astype(int)
        counts = np.bincount(rows * _COLS + cols, minlength=_COLS * _ROWS).reshape(_ROWS, _COLS)
        # A ring split over two tiles (mid-scroll they are off by the scroll)
        # counts with its neighbours; it belongs to the tile holding most of it.
        padded = np.pad(counts, 1)
        around = [padded[r : r + _ROWS, c : c + _COLS] for r in range(3) for c in range(3)]
        most_here = (counts > 0) & (counts == np.maximum.reduce(around))
        is_ring = most_here & (sum(around) >= RING_PIXELS)
        pc, pr = PLAYER_SLOT
        found = [
            Ring(
                (round(gx + (col + 0.5) * tw), round(gy + (row + 0.5) * th)),
                max(abs(int(col) - pc), abs(int(row) - pr)),
            )
            for row, col in zip(*np.nonzero(is_ring), strict=True)
        ]
        return sorted(found, key=lambda ring: ring.tiles_away)
