"""moonlight.perception.corpses: turning loot rings on the game grid."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from moonlight.perception.corpses import Ring, RingWatch
from tests.loot_ring_pictures import BLUE, paint_ring

GRID = (0, 0, 1440, 1056)  # 96 px tiles; the player's tile is x 672-767, y 480-575
NW = Ring((624, 432), 1)  # the middle of the tile north-west of the player


def frame(*rings: tuple[int, int, int]) -> np.ndarray:
    """Grass with ring frames painted at (tile x, tile y, animation frame)."""
    out = np.full((1056, 1440, 3), (40, 120, 60), np.uint8)
    for x, y, phase in rings:
        paint_ring(out, x, y, phase)
    return out


def look(picture: np.ndarray) -> list[Ring]:
    return RingWatch(GRID).rings(picture, 0.0)


def test_ring_on_the_nine_tiles() -> None:
    assert look(frame((576, 384, 0))) == [NW]


def test_every_ring_frame_with_dots_counts() -> None:
    # frames 2-5 and 10-13 are mostly big sparkles, dropped as puffs: half a turn shows
    seen = [phase for phase in range(16) if look(frame((576, 384, phase))) == [NW]]
    assert seen == [0, 1, 6, 7, 8, 9, 14, 15]


def test_specks_that_are_no_ring_are_not_one() -> None:
    rng = np.random.default_rng(0)
    picture = frame()  # an outfit's sparkle or spell smoke: blue specks all over the tile
    for x, y in rng.integers(0, 90, (12, 2)):
        picture[384 + y : 384 + y + 4, 576 + x : 576 + x + 4] = BLUE
    assert look(picture) == []


def test_ring_two_tiles_away_says_so() -> None:
    assert look(frame((480, 480, 0))) == [Ring((528, 528), 2)]


def test_rings_come_nearest_first() -> None:
    assert look(frame((96, 96, 0), (768, 480, 0))) == [Ring((816, 528), 1), Ring((144, 144), 6)]


def test_ring_under_the_player_counts() -> None:
    assert look(frame((672, 480, 6))) == [Ring((720, 528), 0)]


def test_ring_between_two_tiles_is_one_ring() -> None:
    assert look(frame((560, 384, 1))) == [NW]  # mid-step, 16 px of it on the west tile


def test_ring_at_the_grid_edge_counts() -> None:
    assert look(frame((0, 0, 0))) == [Ring((48, 48), 7)]


def test_gravel_is_not_a_ring() -> None:
    gravel = np.full((1056, 1440, 3), 90, np.uint8)
    gravel[::3, ::3] = 220
    assert look(gravel) == []


def test_looks_are_gap_apart() -> None:
    watch = RingWatch(GRID)
    assert watch.rings(frame((576, 384, 0)), 0.0) == [NW]
    assert watch.rings(frame((576, 384, 1)), 0.05) == []  # too soon: no look
    assert watch.rings(frame((576, 384, 1)), 0.1) == [NW]


def test_ring_seen_while_walking() -> None:
    rng = np.random.default_rng(0)
    floor = rng.integers(0, 120, (1056, 1600, 3)).astype(np.uint8)
    floor = np.repeat(np.repeat(floor[::8, ::8], 8, 0), 8, 1)[:1056]  # coarse stone
    walked = floor[:, 40:1480].copy()  # the view scrolled 40 px
    assert look(walked) == []
    assert look(paint_ring(walked, 536, 384, 1)) == [NW]


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
