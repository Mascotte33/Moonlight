"""Video trail: dropping bad matches and writing route lines."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from moonlight.cavebot.pathing import CostMap
from moonlight.cavebot.route_plan import FloorMap
from moonlight.cavebot.video_route import Sample, clean_trail, trail_lines

YELLOW = (0, 255, 255)


def _floors(tmp_path: Path) -> FloorMap:
    """Floors 7 and 8 of tile (0, 0), all walkable, stairs down at (10, 10, 7)."""
    (tmp_path / "cost").mkdir()
    for z, stairs in ((7, (10, 10)), (8, (11, 10))):
        colour = np.full((256, 256, 3), 120, np.uint8)
        colour[stairs[1], stairs[0]] = YELLOW
        cv2.imwrite(str(tmp_path / f"0_0_{z}.png"), colour)
        cv2.imwrite(str(tmp_path / "cost" / f"0_0_{z}.png"), np.full((256, 256, 3), 50, np.uint8))
    return FloorMap(CostMap(tmp_path / "cost"), {})


def test_clean_trail_drops_a_lone_jump() -> None:
    good = [Sample(0, (5, 5, 7)), Sample(1, (6, 5, 7)), Sample(2, (7, 5, 7))]
    bad = Sample(1.5, (90, 90, 7))
    assert clean_trail([good[0], good[1], bad, good[2]]) == good


def test_trail_lines(tmp_path: Path) -> None:
    samples = [Sample(t, (5 + t, 10, 7)) for t in range(5)]  # walk east to (9, 10, 7)
    samples += [Sample(5 + t, (9, 10, 7)) for t in range(6)]  # stand 5 s
    samples += [Sample(11, (12, 10, 8)), Sample(12, (200, 200, 8))]  # stairs down, teleport
    lines = trail_lines(samples, _floors(tmp_path), [((9, 11, 7), "bank (Hunt)")], every_sqm=3)
    assert lines == [
        "5 10 7  # 0:00 start",
        "8 10 7",
        "9 10 7",
        "# 0:04 stood 6 s next to bank (Hunt)",
        "10 10 7 stairs  # 0:11 floor 7 -> 8 (guess)",
        "12 10 8",
        "# 0:12 jumped from 12 10 8 (boat, teleport?)",
        "200 200 8",
    ]
