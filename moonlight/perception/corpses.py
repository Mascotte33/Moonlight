"""Lootable corpses on the game grid, found by their turning loot ring.

The OTS marks a corpse with loot by a ring of small light-blue dots and
X sparkles that turns over the corpse. The dots are a pale blue (blue
20-60 over red, green close to red: RGB about 160, 178, 205), which fire,
skin, white text, skulls and gravel are not, and neither are the river
(blue far over red) or a blue orb (green far over red). They are small
specks; a "puff" cloud has the same color but is one big blob, so blobs
over ``DOT_PIXELS``, and specks next to one, are dropped. The player's own
tile never counts: their outfit sparkles the same blue.

The ring moves and the floor does not. :class:`RingWatch` compares the game
grid with how it looked ``GAP`` seconds before and counts the dots that are
new, tile by tile. While the view scrolls (the player walks) the older look
is first shifted by the scroll (found by phase correlation), so a ring
passed on the way is seen too.

Turning blue specks are not enough: a live amazon's outfit and the smoke of a
spell sparkle the same blue (loot shots 2026-11-04). The ring is one known
32 px animation of 16 frames, a circle of dots around the tile's middle
(the owner's Loot_Effect.gif, kept in ``loot_ring.png``), so a tile counts
only when its specks match one of those frames (:func:`ring_shape`).

# doc: docs/prs/corpse-loot.md#ring-check
# doc: docs/prs/client-hotkeys.md#ring-anywhere
# doc: docs/prs/client-hotkeys.md#ring-color
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np

from moonlight.perception.creatures import PLAYER_SLOT

__all__ = [
    "DOT_PIXELS", "FARTHEST", "GAP", "MOVED", "RING_PIXELS", "RING_SHAPE", "Ring", "RingWatch",
    "ring_shape", "slot_ring",
]

_COLS, _ROWS = 15, 11
#: Most tiles from the player to the grid's edge (its columns are 7 to each side).
FARTHEST = 7
#: Seconds between the two looks; the ring turns visibly in that time.
GAP = 0.1
#: New bright pixels that mean a ring (a real ring gave 41-128, still floor 0).
RING_PIXELS = 25
#: Largest blob of ring color that is a dot (dots measured <= 47, puffs >= 250).
DOT_PIXELS = 60
#: Mean color change of the block above which the view moved (still <= 2.4, walking >= 10).
MOVED = 6.0
#: Least match of a tile's specks with a ring frame (rings 0.52-0.63, fire,
#: grass and spell smoke 0.0-0.44, loot shots 2026-11-04).
RING_SHAPE = 0.48
#: The ring's 16 animation frames, 32 px each, side by side.
_RING_STRIP = cv2.imread(str(Path(__file__).with_name("loot_ring.png")), cv2.IMREAD_GRAYSCALE)
if _RING_STRIP is None:
    raise FileNotFoundError("moonlight/perception/loot_ring.png is missing")
_RING_FRAMES = [
    (_RING_STRIP[:, x : x + 32] > 0).astype(np.float32) for x in range(0, _RING_STRIP.shape[1], 32)
]


def _bright(block: np.ndarray) -> np.ndarray:
    """Loot-ring dots in an RGB *block*: pale blue specks, big blobs dropped."""
    r, g, b = block[..., 0], block[..., 1], block[..., 2]
    blue_over_red = b - r
    color = (r >= 110) & (g >= r) & (b >= g) & (blue_over_red >= 20) & (blue_over_red <= 60)
    color &= g - r <= 35
    count, labels, stats, _ = cv2.connectedComponentsWithStats(color.astype(np.uint8))
    is_dot = stats[:, cv2.CC_STAT_AREA] <= DOT_PIXELS
    is_dot[0] = False  # the background
    is_blob = ~is_dot
    is_blob[0] = False
    # a puff's shading cuts dot-sized bits off its edge: drop what sits by a blob
    near_blob = cv2.dilate(is_blob[labels].astype(np.uint8), np.ones((15, 15), np.uint8))
    out: np.ndarray = is_dot[labels] & (near_blob == 0)
    return out


def ring_shape(specks: np.ndarray) -> float:
    """How well *specks* (a 2x2-tile mask centred on a tile) match a ring frame, up to 1."""
    small = cv2.resize(specks.astype(np.float32), (64, 64), interpolation=cv2.INTER_AREA)
    if not small.any():
        return 0.0
    # Plain correlation: the mean-removed kind scored fire and bare grass 1.0
    # where a spot had no specks at all (loot shots 2026-11-04).
    return max(
        float(cv2.matchTemplate(small, ring, cv2.TM_CCORR_NORMED).max()) for ring in _RING_FRAMES
    )


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
    tiles_away: int  # 0 = the player's own tile, 1 = the 8 around it


def slot_ring(grid: tuple[int, int, int, int], slot: tuple[int, int]) -> Ring:
    """The middle of game-grid *slot* ``(col, row)`` and its distance from the player."""
    gx, gy, gw, gh = grid
    tw, th = gw / _COLS, gh / _ROWS
    (col, row), (pc, pr) = slot, PLAYER_SLOT
    point = (round(gx + (col + 0.5) * tw), round(gy + (row + 0.5) * th))
    return Ring(point, max(abs(col - pc), abs(row - pr)))


class RingWatch:
    """Finds loot rings anywhere on the game grid."""

    def __init__(self, grid: tuple[int, int, int, int]) -> None:
        gx, gy, gw, gh = grid
        tw, th = gw / _COLS, gh / _ROWS
        col, row = PLAYER_SLOT
        self._grid, self._tile_size = grid, (tw, th)
        self._box = (slice(gy, gy + gh), slice(gx, gx + gw))
        self._player = (
            slice(round(row * th), round((row + 1) * th)),
            slice(round(col * tw), round((col + 1) * tw)),
        )
        self._ref: np.ndarray | None = None
        self._ref_at = 0.0

    def rings(self, frame: np.ndarray, now: float) -> list[Ring]:
        """Rings that turned since the last look, nearest first (*frame* is RGB)."""
        if self._ref is not None and now - self._ref_at < GAP:
            return []
        block = frame[self._box].astype(np.int16)
        ref, self._ref, self._ref_at = self._ref, block, now
        whole = block
        if ref is None or ref.shape != block.shape:
            return []
        cut = (0, 0)
        if float(np.abs(block - ref).mean()) > MOVED:
            block, ref, cut = _unscrolled(block, ref, self._player)  # the view scrolled
            if block.size == 0 or float(np.abs(block - ref).mean()) > MOVED:
                return []  # scrolled too far, or not a plain scroll
        ys, xs = np.nonzero(_bright(block) & ~_bright(ref))
        if len(xs) < RING_PIXELS:
            return []
        tw, th = self._tile_size
        cols = np.clip((xs + cut[0]) // tw, 0, _COLS - 1).astype(int)
        rows = np.clip((ys + cut[1]) // th, 0, _ROWS - 1).astype(int)
        counts = np.bincount(rows * _COLS + cols, minlength=_COLS * _ROWS).reshape(_ROWS, _COLS)
        # A ring split over two tiles (mid-scroll they are off by the scroll)
        # counts with its neighbours; it belongs to the tile holding most of it.
        padded = np.pad(counts, 1)
        around = [padded[r : r + _ROWS, c : c + _COLS] for r in range(3) for c in range(3)]
        most_here = (counts > 0) & (counts == np.maximum.reduce(around))
        is_ring = most_here & (sum(around) >= RING_PIXELS)
        # ponytail: the player's own tile counts (owner 2026-11-04: a corpse
        # under the character), though their outfit may sparkle the same
        # blue; mask the outfit if it is clicked with no corpse there.
        found = [
            slot_ring(self._grid, (int(col), int(row)))
            for row, col in zip(*np.nonzero(is_ring), strict=True)
            if ring_shape(_bright(self._around(whole, int(col), int(row)))) >= RING_SHAPE
        ]
        return sorted(found, key=lambda ring: ring.tiles_away)

    def _around(self, block: np.ndarray, col: int, row: int) -> np.ndarray:
        """The 2x2 tiles of *block* centred on tile (*col*, *row*)."""
        # ponytail: cut short at the grid's edge, so a ring there matches
        # worse; pad the block if rings at the edge are missed.
        tw, th = self._tile_size
        x, y = round((col - 0.5) * tw), round((row - 0.5) * th)
        return block[max(y, 0) : round(y + 2 * th), max(x, 0) : round(x + 2 * tw)]
