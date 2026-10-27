"""Health bar finder: synthetic bars and hand-marked golden frames."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from moonlight.perception.creatures import BAR_W, find_creatures

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
