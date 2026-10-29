"""Diagnostics tab: the "what the bot sees" overlay."""

from __future__ import annotations

import numpy as np

from moonlight.core.state import Position
from moonlight.gui.diagnostics_tab import seen_view, slots_of


def test_seen_view_boxes_creatures() -> None:
    frame = np.zeros((40 + 110, 10 + 150, 3), np.uint8)
    view = seen_view(frame, (10, 40, 150, 110), [(1, 1)])
    assert view.shape == (110, 150, 3)
    assert tuple(view[13, 15]) == (81, 93, 229)  # red box edge on slot (1, 1)
    assert not view[15, 55].any()  # empty slot (5, 1) stays dark


def test_seen_view_draws_path_and_corpses() -> None:
    frame = np.zeros((110, 150, 3), np.uint8)
    view = seen_view(frame, (0, 0, 150, 110), [], path=[(9, 5)], corpses=[(2, 2)])
    assert tuple(view[55, 85]) == (255, 120, 0)  # blue line from the player (7, 5) east
    assert tuple(view[25, 21]) == (0, 215, 255)  # gold circle on slot (2, 2)


def test_slots_of_drops_tiles_off_the_grid() -> None:
    player = Position(100, 200, 7)
    assert slots_of([(101, 200), (120, 200)], player) == [(8, 5)]
