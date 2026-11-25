"""Stamina read from the Skills window."""

from pathlib import Path

import cv2
import numpy as np

from moonlight.perception.stamina import LevelReader, StaminaReader

SKILLS = Path(__file__).with_name("stamina_skills.png")  # the Skills window at 41:23


def frame_with_skills(x: int, y: int) -> np.ndarray:
    frame = np.full((400, 500, 3), 40, np.uint8)
    bgr = cv2.imread(str(SKILLS))
    assert bgr is not None
    skills = bgr[:, :, ::-1]
    frame[y : y + skills.shape[0], x : x + skills.shape[1]] = skills
    return frame


def test_stamina_is_read_wherever_the_window_is() -> None:
    frames = [frame_with_skills(10, 20), frame_with_skills(300, 150)]
    reader = StaminaReader(lambda: frames[0])
    assert reader.minutes() == 41 * 60 + 23
    frames.pop(0)  # the window moved: found again
    assert reader.minutes() == 41 * 60 + 23


def test_no_skills_window_is_unknown() -> None:
    reader = StaminaReader(lambda: np.full((400, 500, 3), 40, np.uint8))
    assert reader.minutes() is None


def test_level_is_read_wherever_the_window_is() -> None:
    frames = [frame_with_skills(10, 20), frame_with_skills(300, 150)]
    reader = LevelReader(lambda: frames[0])
    assert reader.level() == 65
    frames.pop(0)
    assert reader.level() == 65


def test_no_skills_window_is_an_unknown_level() -> None:
    assert LevelReader(lambda: np.full((400, 500, 3), 40, np.uint8)).level() is None


# doc: docs/prs/stamina-switch.md#tight-font
def test_the_tight_drawing_of_level_and_stamina_reads() -> None:
    from moonlight.perception.stamina import (
        _LABEL_TIGHT,
        _LABELS,
        _LEVEL_LABEL_TIGHT,
        _LEVEL_LABELS,
        _locate_any,
    )

    for label, labels in ((_LEVEL_LABEL_TIGHT, _LEVEL_LABELS), (_LABEL_TIGHT, _LABELS)):
        lit = np.zeros((40, 120), dtype=bool)
        lit[10 : 10 + label.shape[0], 20 : 20 + label.shape[1]] = label
        found = _locate_any(lit, labels, None)
        assert found is not None and found[0] == (20, 10) and found[1] is label
