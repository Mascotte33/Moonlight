"""Status icon reader (H1.F1.K1)."""

from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from moonlight.calibration.calibrate import CalibrationProfile, Region
from moonlight.perception.status_icons import CONDITION_TEMPLATES_DIR, StatusIconReader

REGION = (20, 10, 108, 12)
CONDITIONS = ("poison", "burn", "electrify", "paralyse", "bleeding")


@pytest.fixture
def reader() -> StatusIconReader:
    profile = CalibrationProfile(landmark=(0, 0))
    profile.set_region("status_icons", Region(*REGION))
    return StatusIconReader(profile)


def frame_with(*placed: tuple[str, int]) -> np.ndarray:
    """Dark RGB frame with each (condition, slot) tile alpha-blended in."""
    frame = np.full((60, 200, 3), 20, dtype=np.uint8)
    for name, slot in placed:
        bgra = cv2.imread(str(CONDITION_TEMPLATES_DIR / f"{name}.png"), cv2.IMREAD_UNCHANGED)
        assert bgra is not None
        rgb = bgra[:, :, 2::-1].astype(np.float32)
        alpha = bgra[:, :, 3:].astype(np.float32) / 255.0
        x, y = REGION[0] + 2 + 10 * slot, REGION[1] + 2
        patch = frame[y : y + 9, x : x + 9].astype(np.float32)
        frame[y : y + 9, x : x + 9] = (alpha * rgb + (1 - alpha) * patch).astype(np.uint8)
    return frame


def test_no_icons(reader: StatusIconReader) -> None:
    assert reader.read_active_conditions(frame_with()) == frozenset()


@pytest.mark.parametrize("name", CONDITIONS)
def test_each_condition_alone(reader: StatusIconReader, name: str) -> None:
    assert reader.read_active_conditions(frame_with((name, 1))) == {name}


def test_two_at_once(reader: StatusIconReader) -> None:
    frame = frame_with(("poison", 0), ("bleeding", 3))
    assert reader.read_active_conditions(frame) == {"poison", "bleeding"}


def test_shifted_by_a_pixel(reader: StatusIconReader) -> None:
    frame = frame_with(("burn", 1))
    shifted = np.roll(frame, shift=(1, 1), axis=(0, 1))
    assert reader.read_active_conditions(shifted) == {"burn"}


def test_missing_template_raises(tmp_path: Path) -> None:
    for path in CONDITION_TEMPLATES_DIR.glob("*.png"):
        if path.stem != "burn":
            shutil.copy(path, tmp_path)
    profile = CalibrationProfile(landmark=(0, 0))
    profile.set_region("status_icons", Region(*REGION))
    with pytest.raises(FileNotFoundError):
        StatusIconReader(profile, tmp_path)
