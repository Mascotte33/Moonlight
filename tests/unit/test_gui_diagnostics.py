"""Diagnostics tab: the "what the bot sees" overlay."""

from __future__ import annotations

import numpy as np

from moonlight.gui.tabs.diagnostics import seen_view


def test_seen_view_boxes_creatures_and_predictions() -> None:
    frame = np.zeros((40 + 110, 10 + 150, 3), np.uint8)
    view = seen_view(frame, (10, 40, 150, 110), [(1, 1)], {(3, 1)})
    assert view.shape == (110, 150, 3)
    assert tuple(view[13, 15]) == (81, 93, 229)  # red box edge on slot (1, 1)
    assert tuple(view[13, 35]) == (62, 155, 201)  # orange box edge on slot (3, 1)
    assert not view[15, 55].any()  # empty slot (5, 1) stays dark
