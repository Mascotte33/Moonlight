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


@pytest.mark.skipif(not any(GOLDEN.glob("*.png")), reason="golden screenshots not present")
@pytest.mark.parametrize("name", ["hp_bar", "mp_bar"])
def test_bar_finders_match_the_profile_on_golden_screenshots(name: str) -> None:
    expected = json.loads(PROFILE.read_text(encoding="utf-8"))["regions"][name]
    finder = dict(FINDERS)[name]
    labels = json.loads((GOLDEN / "labels.json").read_text(encoding="utf-8"))["frames"]
    off = []
    for path in sorted(GOLDEN.glob("*.png")):
        if "layout-mismatch" in str(labels.get(path.name, {}).get("notes", "")):
            continue  # a different window layout than the profile
        shot = cv2.imread(str(path))
        assert shot is not None
        box = finder(shot)
        if box is None or max(abs(a - b) for a, b in zip(box, expected, strict=True)) > 3:
            off.append((path.name, box))
    assert not off


ANCHORS = Path("data/templates/anchors")


@pytest.mark.skipif(
    not any(GOLDEN.glob("*.png")) or not ANCHORS.is_dir(),
    reason="golden screenshots or reference images not present",
)
def test_battle_list_finder_on_golden_screenshots() -> None:
    # Two characters keep the list at different heights on screen, so only x, w
    # and h are compared with the profile. 124 of the frames show the list.
    expected = json.loads(PROFILE.read_text(encoding="utf-8"))["regions"]["battle_list"]
    finder = dict(FINDERS)["battle_list"]
    shots = [cv2.imread(str(p)) for p in sorted(GOLDEN.glob("*.png"))]
    boxes = [finder(s) for s in shots if s is not None]
    found = [b for b in boxes if b is not None]
    assert len(found) == 124
    off = [b for b in found if max(abs(b[i] - expected[i]) for i in (0, 2, 3)) > 3]
    assert not off
    full = cv2.imread(str(GOLDEN / "battle" / "full.png"))
    if full is not None:
        assert finder(full) == (2212, 33, 156, 473)
