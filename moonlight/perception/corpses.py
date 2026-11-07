"""Lootable corpses on the game grid, found by their turning loot ring.

The OTS marks a corpse with loot by a ring of small light-blue dots and
X sparkles that turns over the corpse. The dots are a pale blue (blue
20-60 over red, green close to red: RGB about 160, 178, 205), which fire,
skin, white text, skulls and gravel are not, and neither are the river
(blue far over red) or a blue orb (green far over red). They are small
specks; a "puff" cloud has the same color but is one big blob, so blobs
over ``DOT_PIXELS``, and specks next to one, are dropped.

Blue specks are not enough: a live amazon's outfit and the smoke of a
spell sparkle the same blue (loot shots 2026-11-04). The ring is one known
32 px animation of 16 frames, a circle of dots around the tile's middle
(the owner's Loot_Effect.gif, kept in ``loot_ring.png``), so a tile counts
only when its specks match one of those frames (:func:`ring_shape`). The
shape alone is enough, so a ring is seen while the view scrolls too (the
character walks on right after a kill, bot.log 2026-11-04).

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
    "DOT_PIXELS", "FARTHEST", "GAP", "RING_SHAPE", "Ring", "RingWatch", "ring_shape", "slot_ring",
]

_COLS, _ROWS = 15, 11
#: Most tiles from the player to the grid's edge (its columns are 7 to each side).
FARTHEST = 7
#: Least seconds between two looks (a look costs about 45 ms).
GAP = 0.1
#: Largest blob of ring color that is a dot (dots measured <= 47, puffs >= 250).
DOT_PIXELS = 60
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
        self._grid, self._tile_size = grid, (gw / _COLS, gh / _ROWS)
        self._box = (slice(gy, gy + gh), slice(gx, gx + gw))
        self._looked_at = float("-inf")

    def rings(self, frame: np.ndarray, now: float) -> list[Ring]:
        """Loot rings on the game grid, nearest first (*frame* is RGB)."""
        if now - self._looked_at < GAP:
            return []
        self._looked_at = now
        tw, th = self._tile_size
        # half a tile of blank around the grid: a ring at its edge is matched whole
        pad_x, pad_y = round(tw / 2), round(th / 2)
        specks = np.pad(_bright(frame[self._box].astype(np.int16)), ((pad_y,), (pad_x,)))
        scores = np.array([
            [
                ring_shape(specks[
                    round(row * th) : round(row * th + 2 * th),
                    round(col * tw) : round(col * tw + 2 * tw),
                ])
                for col in range(_COLS)
            ]
            for row in range(_ROWS)
        ])
        # A ring between two tiles (mid-step) matches on both; it belongs to the better one.
        padded = np.pad(scores, 1)
        around = [padded[r : r + _ROWS, c : c + _COLS] for r in range(3) for c in range(3)]
        is_ring = (scores >= RING_SHAPE) & (scores == np.maximum.reduce(around))
        # ponytail: the player's own tile counts (owner 2026-11-04: a corpse
        # under the character), though their outfit may sparkle the same
        # blue; mask the outfit if it is clicked with no corpse there.
        found = [
            slot_ring(self._grid, (int(col), int(row)))
            for row, col in zip(*np.nonzero(is_ring), strict=True)
        ]
        return sorted(found, key=lambda ring: ring.tiles_away)
