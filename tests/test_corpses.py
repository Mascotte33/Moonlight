"""moonlight.perception.corpses: a turning loot ring on the 9 tiles around the player."""

from __future__ import annotations

import numpy as np

from moonlight.perception.corpses import RingWatch

GRID = (0, 0, 1440, 1056)  # 96 px tiles; the player's tile is x 672-767, y 480-575
BLUE = (255, 230, 200)  # BGR light blue


def frame(*dots: tuple[int, int]) -> np.ndarray:
    out = np.full((1056, 1440, 3), (40, 120, 60), np.uint8)  # grass
    for x, y in dots:
        out[y : y + 5, x : x + 8] = BLUE
    return out


def looks(*frames: np.ndarray) -> list[bool]:
    watch = RingWatch(GRID)
    return [watch.seen(f, i * 0.1) for i, f in enumerate(frames)]


def test_turning_ring_on_the_nine_tiles() -> None:
    assert looks(frame((600, 400)), frame((620, 420))) == [False, True]  # north-west tile


def test_still_dots_are_floor() -> None:
    assert looks(frame((600, 400)), frame((600, 400))) == [False, False]


def test_ring_two_tiles_away_is_out_of_reach() -> None:
    assert looks(frame((500, 500)), frame((520, 520))) == [False, False]


def test_scrolling_view_is_not_a_ring() -> None:
    gravel = np.full((1056, 1440, 3), 90, np.uint8)
    gravel[::3, ::3] = 220
    assert looks(gravel, np.roll(gravel, 1, axis=0)) == [False, False]


def test_looks_are_gap_apart() -> None:
    watch = RingWatch(GRID)
    assert not watch.seen(frame((600, 400)), 0.0)
    assert not watch.seen(frame((620, 420)), 0.05)  # too soon: no look
    assert watch.seen(frame((620, 420)), 0.1)
