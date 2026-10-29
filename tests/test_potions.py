"""Offline tests for the action bar potion counter."""

# doc: docs/prs/potion-count.md#potion-count

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from moonlight.perception.potions import DIGITS, PotionCounter, read_count

GLYPHS = {d: g for g, d in DIGITS.items()}


def slot(seed: int, count: str) -> np.ndarray:
    """A dark 31x35 slot picture with *count* drawn in the bar font."""
    pic = np.random.default_rng(seed).integers(0, 120, (31, 35, 3), dtype=np.uint8)
    x = 4
    for d in count:
        rows = GLYPHS[d].split("/")
        for dy, row in enumerate(rows):
            for dx, ch in enumerate(row):
                if ch == "#":
                    pic[24 + dy, x + dx] = 223
        x += len(rows[0]) + 1
    return pic


def save(tmp_path: Path, kind: str, pic: np.ndarray) -> None:
    alpha = np.full(pic.shape[:2], 255, np.uint8)
    alpha[22:, 4:] = 0  # the count
    (tmp_path / kind).mkdir(exist_ok=True)
    rgba = np.dstack([cv2.cvtColor(pic, cv2.COLOR_RGB2BGR), alpha])
    cv2.imwrite(str(tmp_path / kind / "p.png"), rgba)


def test_reads_the_bar_digits() -> None:
    assert read_count(slot(0, "105")) == 105
    assert read_count(slot(0, "88")) == 88
    assert read_count(slot(0, "2346")) == 2346
    assert read_count(slot(0, "79")) == 79
    assert read_count(slot(0, "")) is None


def test_counts_spirit_for_both_and_refinds_a_moved_slot(tmp_path: Path) -> None:
    health, spirit = slot(1, "105"), slot(2, "8")
    save(tmp_path, "health", health)
    save(tmp_path, "spirit", spirit)
    frame = np.zeros((300, 400, 3), np.uint8)
    frame[200:231, 50:85] = health
    frame[200:231, 100:135] = spirit
    counter = PotionCounter(lambda: frame, tmp_path)
    assert counter.counts() == {"health": 113, "mana": 8}
    frame[:] = 0
    frame[100:131, 300:335] = slot(1, "50")
    assert counter.counts() == {"health": 50, "mana": None}


def test_low_needs_a_known_count_under_its_threshold(tmp_path: Path) -> None:
    save(tmp_path, "mana", slot(3, "88"))
    frame = np.zeros((100, 100, 3), np.uint8)
    frame[10:41, 10:45] = slot(3, "88")
    now = [0.0]
    counter = PotionCounter(lambda: frame, tmp_path, clock=lambda: now[0])
    assert not counter.low(0, 50)
    now[0] = 10.0
    assert counter.low(500, 100)  # health unknown, mana 88 < 100
    now[0] = 20.0
    frame[34:40, 14:40] = 223  # a count that does not read
    assert not counter.low(0, 100)
