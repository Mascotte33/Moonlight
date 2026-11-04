"""moonlight.perception.corpses: turning loot rings on the game grid."""

from __future__ import annotations

import numpy as np

from moonlight.perception.corpses import Ring, RingWatch, _overlap

GRID = (0, 0, 1440, 1056)  # 96 px tiles; the player's tile is x 672-767, y 480-575
BLUE = (255, 230, 200)  # BGR light blue
NW = Ring((624, 432), 1)  # the middle of the tile north-west of the player


def frame(*dots: tuple[int, int]) -> np.ndarray:
    out = np.full((1056, 1440, 3), (40, 120, 60), np.uint8)  # grass
    for x, y in dots:
        out[y : y + 5, x : x + 8] = BLUE
    return out


def looks(*frames: np.ndarray) -> list[list[Ring]]:
    watch = RingWatch(GRID)
    return [watch.rings(f, i * 0.1) for i, f in enumerate(frames)]


def test_turning_ring_on_the_nine_tiles() -> None:
    assert looks(frame((600, 400)), frame((620, 420))) == [[], [NW]]


def test_still_dots_are_floor() -> None:
    assert looks(frame((600, 400)), frame((600, 400))) == [[], []]


def test_ring_two_tiles_away_says_so() -> None:
    assert looks(frame((500, 500)), frame((520, 520))) == [[], [Ring((528, 528), 2)]]


def test_rings_come_nearest_first() -> None:
    far, own = ((20, 20), (40, 40)), ((700, 500), (720, 520))
    assert looks(frame(far[0], own[0]), frame(far[1], own[1]))[1] == [
        Ring((720, 528), 0),
        Ring((48, 48), 7),
    ]


def test_ring_split_over_two_tiles_is_one_ring() -> None:
    dots = [(x, 470) for x in range(560, 600, 8)]  # 3 dots on the NW tile's row, 2 on its west
    assert looks(frame(), frame(*dots))[1] == [Ring((624, 432), 1)]


def test_scrolling_view_is_not_a_ring() -> None:
    gravel = np.full((1056, 1440, 3), 90, np.uint8)
    gravel[::3, ::3] = 220
    assert looks(gravel, np.roll(gravel, 1, axis=0)) == [[], []]


def test_looks_are_gap_apart() -> None:
    watch = RingWatch(GRID)
    assert watch.rings(frame((600, 400)), 0.0) == []
    assert watch.rings(frame((620, 420)), 0.05) == []  # too soon: no look
    assert watch.rings(frame((620, 420)), 0.1) == [NW]


def test_ring_on_the_south_east_tile() -> None:
    assert looks(frame((800, 600)), frame((820, 620))) == [[], [Ring((816, 624), 1)]]


def test_ring_passed_while_walking() -> None:
    rng = np.random.default_rng(0)
    floor = rng.integers(0, 120, (1056, 1600, 3)).astype(np.uint8)
    floor = np.repeat(np.repeat(floor[::8, ::8], 8, 0), 8, 1)[:1056]  # coarse stone
    before, after = floor[:, :1440].copy(), floor[:, 40:1480].copy()  # walked east 40 px
    before[400:405, 600:608] = BLUE
    after[420:425, 580:588] = BLUE  # the ring turned on the floor that scrolled by
    assert looks(before, after) == [[], [NW]]
    assert looks(before, floor[:, 40:1480].copy()) == [[], []]  # just walking


def test_overlap_of_a_scroll_past_the_block_is_empty() -> None:
    for d in (-300, 300):
        new, old = _overlap(270, d)
        assert len(range(270)[new]) == len(range(270)[old]) == 0
