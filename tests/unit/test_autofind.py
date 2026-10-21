"""Tests for moonlight.setup.autofind."""

from __future__ import annotations

import numpy as np

from moonlight.setup.autofind import find_regions


def _broken(_shot: np.ndarray) -> None:
    raise ValueError("boom")


def test_found_and_missing_areas_are_split() -> None:
    shot = np.zeros((10, 10, 3), np.uint8)
    finders = [
        ("hp_bar", lambda _s: (1, 2, 3, 4)),
        ("mp_bar", lambda _s: None),
        ("minimap", _broken),
    ]
    assert find_regions(shot, finders) == ({"hp_bar": (1, 2, 3, 4)}, ["mp_bar", "minimap"])
