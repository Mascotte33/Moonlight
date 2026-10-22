"""Tests for moonlight.setup.autofind."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from moonlight.setup.autofind import FINDERS, find_regions


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


GOLDEN = Path(__file__).resolve().parents[1] / "golden"
PROFILE = Path(__file__).resolve().parents[1] / "golden_profile" / "moonlight_user.json"
ANCHORS = Path("data/templates/anchors")
IMAGE_FINDERS = {"battle_list", "minimap", "floor_indicator", "status_icons"}


@pytest.mark.skipif(not any(GOLDEN.glob("*.png")), reason="golden screenshots not present")
def test_finders_match_the_profile_on_golden_screenshots() -> None:
    # One pass over the screenshots, since reading 200 of them per finder is slow.
    expected = json.loads(PROFILE.read_text(encoding="utf-8"))["regions"]
    labels = json.loads((GOLDEN / "labels.json").read_text(encoding="utf-8"))["frames"]
    finders = [(n, f) for n, f in FINDERS if ANCHORS.is_dir() or n not in IMAGE_FINDERS]
    off = []
    battle_lists = 0
    for path in sorted(GOLDEN.glob("*.png")):
        if "layout-mismatch" in str(labels.get(path.name, {}).get("notes", "")):
            continue  # a different window layout than the profile
        shot = cv2.imread(str(path))
        assert shot is not None
        for name, finder in finders:
            box = finder(shot)
            # Two characters keep the battle list at different heights on screen,
            # so its y isn't compared, and frames without the list return None.
            dims = (0, 2, 3) if name == "battle_list" else (0, 1, 2, 3)
            if name == "battle_list" and box is None:
                continue
            battle_lists += name == "battle_list"
            if box is None or max(abs(box[i] - expected[name][i]) for i in dims) > 3:
                off.append((path.name, name, box))
    assert not off
    if ANCHORS.is_dir():
        assert battle_lists == 123  # plus the skipped layout-mismatch frame


@pytest.mark.skipif(not ANCHORS.is_dir(), reason="reference images not present")
def test_battle_list_height_on_a_tall_window() -> None:
    full = cv2.imread(str(GOLDEN / "battle" / "full.png"))
    if full is None:
        pytest.skip("battle list screenshot not present")
    assert dict(FINDERS)["battle_list"](full) == (2212, 33, 156, 473)
