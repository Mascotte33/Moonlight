"""Reading the client's chase mode from the side-panel buttons.

# doc: docs/prs/targeting.md#chase-mode
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from moonlight.perception.chase_mode import read_chase_mode

REGION = (10, 20, 20, 44)
RED, GRAY = (220, 40, 40), (180, 180, 180)


def side_panel(stand_icon: tuple[int, int, int], chase_icon: tuple[int, int, int]) -> np.ndarray:
    frame = np.full((100, 60, 3), 60, dtype=np.uint8)
    frame[25:37, 15:25] = stand_icon
    frame[47:59, 15:25] = chase_icon
    return frame


def test_the_red_icon_is_the_active_mode() -> None:
    assert read_chase_mode(side_panel(RED, GRAY), REGION) == "none"
    assert read_chase_mode(side_panel(GRAY, RED), REGION) == "follow"


def test_no_or_two_red_icons_is_unread() -> None:
    assert read_chase_mode(side_panel(GRAY, GRAY), REGION) is None
    assert read_chase_mode(side_panel(RED, RED), REGION) is None


GOLDEN = sorted((Path(__file__).parent / "golden").glob("*LevelUp_1.png"))


@pytest.mark.skipif(not GOLDEN, reason="golden screenshots not present")
def test_a_real_client_in_stand_still_and_its_buttons_swapped() -> None:
    import cv2

    from moonlight.calibration.autofind import find_chase_buttons

    screenshot = cv2.imread(str(GOLDEN[0]))
    assert screenshot is not None
    frame = cv2.cvtColor(screenshot, cv2.COLOR_BGR2RGB)
    region = find_chase_buttons(frame)
    assert region is not None
    assert read_chase_mode(frame, region) == "none"
    x, y, w, h = region
    buttons = frame[y : y + h, x : x + w].copy()
    half = h // 2
    frame[y : y + half, x : x + w] = buttons[half : 2 * half]
    frame[y + half : y + 2 * half, x : x + w] = buttons[:half]
    assert read_chase_mode(frame, region) == "follow"
