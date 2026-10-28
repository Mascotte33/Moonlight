"""Lootable corpses next to the player, found by their turning loot ring.

The OTS marks a corpse with loot by a ring of small white and light-blue
dots that turns and sparkles. One frame cannot tell those dots from light
stone or gravel, which look the same; but the ring moves and the floor does
not. :class:`RingWatch` compares the 3x3 tiles around the player (its own
tile and the 8 around it) with how they looked ``GAP`` seconds before and
counts the bright dots that are new. While the view scrolls (the player
walks) everything changes, so no ring is reported then.

# doc: docs/prs/corpse-loot.md#ring-check
"""

from __future__ import annotations

import numpy as np

from moonlight.perception.creatures import PLAYER_SLOT

__all__ = ["GAP", "MOVED", "RING_PIXELS", "RingWatch"]

_COLS, _ROWS = 15, 11
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


class RingWatch:
    """Reports a loot ring on the player's tile or the 8 around it."""

    def __init__(self, grid: tuple[int, int, int, int]) -> None:
        gx, gy, gw, gh = grid
        tw, th = gw / _COLS, gh / _ROWS
        col, row = PLAYER_SLOT
        x0, y0 = round(gx + (col - 1) * tw), round(gy + (row - 1) * th)
        self._box = (slice(y0, y0 + round(3 * th)), slice(x0, x0 + round(3 * tw)))
        self._ref: np.ndarray | None = None
        self._ref_at = 0.0

    def seen(self, frame: np.ndarray, now: float) -> bool:
        """True when a ring turned in the block since the last look (BGR *frame*)."""
        if self._ref is not None and now - self._ref_at < GAP:
            return False
        block = frame[self._box].astype(np.int16)
        ref, self._ref, self._ref_at = self._ref, block, now
        if ref is None or ref.shape != block.shape:
            return False
        if float(np.abs(block - ref).mean()) > MOVED:
            return False  # the view scrolled
        # ponytail: a white spell flash on these tiles counts too; it only
        # costs one extra loot press.
        return int((_bright(block) & ~_bright(ref)).sum()) >= RING_PIXELS
