"""moonlight.perception.corpses: turning loot rings on the game grid."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from moonlight.perception.corpses import Ring, RingWatch, _overlap
from tests.loot_ring_pictures import BLUE, paint_ring

GRID = (0, 0, 1440, 1056)  # 96 px tiles; the player's tile is x 672-767, y 480-575
NW = Ring((624, 432), 1)  # the middle of the tile north-west of the player


def frame(*rings: tuple[int, int, int]) -> np.ndarray:
    """Grass with ring frames painted at (tile x, tile y, animation frame)."""
    out = np.full((1056, 1440, 3), (40, 120, 60), np.uint8)
    for x, y, phase in rings:
        paint_ring(out, x, y, phase)
    return out


def looks(*frames: np.ndarray) -> list[list[Ring]]:
    watch = RingWatch(GRID)
    return [watch.rings(f, i * 0.1) for i, f in enumerate(frames)]


def test_turning_ring_on_the_nine_tiles() -> None:
    assert looks(frame((576, 384, 0)), frame((576, 384, 1))) == [[], [NW]]


def test_still_ring_is_floor() -> None:
    assert looks(frame((576, 384, 0)), frame((576, 384, 0))) == [[], []]


def test_turning_specks_that_are_no_ring_are_not_one() -> None:
    rng = np.random.default_rng(0)
    pictures = []
    for _ in range(2):  # an outfit's sparkle or spell smoke: blue specks all over the tile
        picture = frame()
        for x, y in rng.integers(0, 90, (12, 2)):
            picture[384 + y : 384 + y + 4, 576 + x : 576 + x + 4] = BLUE
        pictures.append(picture)
    assert looks(*pictures) == [[], []]


def test_ring_two_tiles_away_says_so() -> None:
    assert looks(frame((480, 480, 0)), frame((480, 480, 1))) == [[], [Ring((528, 528), 2)]]


def test_rings_come_nearest_first() -> None:
    assert looks(frame((96, 96, 0), (768, 480, 0)), frame((96, 96, 1), (768, 480, 1)))[1] == [
        Ring((816, 528), 1),
        Ring((144, 144), 6),
    ]


def test_ring_under_the_player_counts() -> None:
    assert looks(frame((672, 480, 5)), frame((672, 480, 6)))[1] == [Ring((720, 528), 0)]


def test_ring_split_over_two_tiles_is_one_ring() -> None:
    assert looks(frame(), frame((560, 384, 2)))[1] == [NW]  # 16 px of it on the west tile


def test_scrolling_view_is_not_a_ring() -> None:
    gravel = np.full((1056, 1440, 3), 90, np.uint8)
    gravel[::3, ::3] = 220
    assert looks(gravel, np.roll(gravel, 1, axis=0)) == [[], []]


def test_looks_are_gap_apart() -> None:
    watch = RingWatch(GRID)
    assert watch.rings(frame((576, 384, 0)), 0.0) == []
    assert watch.rings(frame((576, 384, 1)), 0.05) == []  # too soon: no look
    assert watch.rings(frame((576, 384, 1)), 0.1) == [NW]


def test_ring_on_the_south_east_tile() -> None:
    assert looks(frame((768, 576, 7)), frame((768, 576, 8))) == [[], [Ring((816, 624), 1)]]


def test_ring_passed_while_walking() -> None:
    rng = np.random.default_rng(0)
    floor = rng.integers(0, 120, (1056, 1600, 3)).astype(np.uint8)
    floor = np.repeat(np.repeat(floor[::8, ::8], 8, 0), 8, 1)[:1056]  # coarse stone
    before, after = floor[:, :1440].copy(), floor[:, 40:1480].copy()  # walked east 40 px
    paint_ring(before, 576, 384, 0)
    paint_ring(after, 536, 384, 1)  # the ring turned on the floor that scrolled by
    assert looks(before, after) == [[], [NW]]
    assert looks(before, floor[:, 40:1480].copy()) == [[], []]  # just walking


def test_overlap_of_a_scroll_past_the_block_is_empty() -> None:
    for d in (-300, 300):
        new, old = _overlap(270, d)
        assert len(range(270)[new]) == len(range(270)[old]) == 0


def test_live_ring_is_found_and_the_campfire_is_not() -> None:
    # tests/loot_ring_live.png: two looks 0.1 s apart (top, bottom) at grid
    # x 196-687, y 0-343 of a 1473x1080 grid: a ringed amazon corpse at tile
    # (3, 1) and a campfire at tile (5, 2).
    saved = cv2.imread(str(Path(__file__).with_name("loot_ring_live.png")))
    assert saved is not None
    looks_stacked = saved[..., ::-1]  # saved BGR, the bot sees RGB
    watch, found = RingWatch((0, 0, 1473, 1080)), list[Ring]()
    for top in (0, 344):
        frame = np.zeros((1080, 1473, 3), np.uint8)
        frame[0:344, 196:688] = looks_stacked[top : top + 344]
        found = watch.rings(frame, top / 3440)
    assert found == [Ring((344, 147), 4)]
