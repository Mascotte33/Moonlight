"""Health bar finder: synthetic bars and hand-marked golden frames."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from moonlight.perception.creatures import BAR_W, CreatureTracker, creature_bars, find_creatures

GOLDEN = Path(__file__).resolve().parents[0] / "golden"
GRID = (0, 0, 15 * 96, 11 * 96)


def _bar(frame: np.ndarray, x: int, y: int, fill: int = BAR_W - 2) -> None:
    frame[y : y + 4, x : x + BAR_W] = 0
    frame[y + 1 : y + 3, x + 1 : x + 1 + fill] = (0, 192, 0)


def _frame() -> np.ndarray:
    return np.full((GRID[3], GRID[2], 3), 90, np.uint8)


def test_bar_maps_to_the_tile_below_it() -> None:
    frame = _frame()
    _bar(frame, 3 * 96 + 33, 2 * 96 - 9)  # over tile (3, 2), like the client
    _bar(frame, 10 * 96 + 33, 8 * 96 - 9, fill=5)
    assert find_creatures(frame, GRID) == [(3, 2), (10, 8)]


def test_bar_fill_is_the_creatures_hp() -> None:
    frame = _frame()
    _bar(frame, 3 * 96 + 33, 2 * 96 - 9)
    _bar(frame, 10 * 96 + 33, 8 * 96 - 9, fill=(BAR_W - 2) // 2 + 1)
    bars = creature_bars(frame, GRID)
    assert bars[(3, 2)] == 100.0
    assert 50 < bars[(10, 8)] < 55


def test_player_slot_and_black_boxes_are_ignored() -> None:
    frame = _frame()
    _bar(frame, 7 * 96 + 33, 5 * 96 - 9)
    frame[100:120, 100:200] = 0  # a black area is no bar
    assert find_creatures(frame, GRID) == []


GOLDEN_SLOTS = {
    "2026-07-06_004730924_Old Wrath_LevelUp_2.png": [
        (3, 1), (6, 9), (9, 9), (10, 6), (10, 7), (11, 4), (11, 6),
    ],
    "2026-07-05_234619094_Secco Cio_LevelUp_4.png": [(8, 3)],
    "2026-07-06_005659257_Old Wrath_LevelUp_6.png": [],
}  # fmt: skip


@pytest.mark.skipif(not any(GOLDEN.glob("*.png")), reason="golden screenshots not present")
@pytest.mark.parametrize(("name", "slots"), GOLDEN_SLOTS.items())
def test_golden_frames(name: str, slots: list[tuple[int, int]]) -> None:
    frame = cv2.imread(str(GOLDEN / name))
    assert frame is not None
    assert find_creatures(frame, (418, 61, 1440, 1056)) == slots


def _textured_frame(shift: tuple[int, int] = (0, 0)) -> np.ndarray:
    """A game view with ground texture, slid by *shift* px like a walking player's view."""
    noise = np.random.default_rng(7).integers(60, 200, (GRID[3] + 40, GRID[2] + 40), np.uint8)
    dx, dy = shift
    ground = noise[20 - dy : 20 - dy + GRID[3], 20 - dx : 20 - dx + GRID[2]]
    return np.repeat(ground[:, :, None], 3, axis=2).copy()


def test_a_creature_walking_right_is_on_the_tile_it_walks_into() -> None:
    tracker = CreatureTracker()
    before, after = _textured_frame(), _textured_frame()
    # Two frames into a step from tile (3, 2) to (4, 2): drawn 80 then 70 px left of (4, 2).
    _bar(before, 4 * 96 + 33 - 80, 2 * 96 - 9)
    _bar(after, 4 * 96 + 33 - 70, 2 * 96 - 9)
    assert list(tracker.read(before, GRID)) == [(3, 2)]  # first frame: no motion seen yet
    assert list(tracker.read(after, GRID)) == [(4, 2)]


def test_a_creature_walking_up_is_on_the_tile_it_walks_into() -> None:
    tracker = CreatureTracker()
    before, after = _textured_frame(), _textured_frame()
    _bar(before, 3 * 96 + 33, 2 * 96 - 9 + 80)  # from (3, 3) to (3, 2)
    _bar(after, 3 * 96 + 33, 2 * 96 - 9 + 70)
    tracker.read(before, GRID)
    assert list(tracker.read(after, GRID)) == [(3, 2)]


def test_a_standing_creature_stays_on_its_tile() -> None:
    tracker = CreatureTracker()
    frame = _textured_frame()
    _bar(frame, 4 * 96 + 33 - 10, 2 * 96 - 9)
    tracker.read(frame, GRID)
    assert list(tracker.read(frame, GRID)) == [(4, 2)]


def test_no_creature_is_moved_ahead_while_the_player_walks() -> None:
    tracker = CreatureTracker()
    before, after = _textured_frame(), _textured_frame(shift=(10, 0))  # the view slides right
    _bar(before, 4 * 96 + 33 - 80, 2 * 96 - 9)
    _bar(after, 4 * 96 + 33 - 70, 2 * 96 - 9)
    tracker.read(before, GRID)
    assert list(tracker.read(after, GRID)) == [(3, 2)]
