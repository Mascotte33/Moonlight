"""The max mana read off the MP bar's ``115/295`` text (live client shot 2026-11-04)."""

# doc: docs/prs/client-hotkeys.md#mana-points

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from moonlight.perception.logocr import health
from moonlight.perception.vitals import VitalsReader

SHOT = Path(__file__).with_name("mp_bar_115_of_295.png")  # the bar at x 8-1095, y 5-16


def shot() -> np.ndarray:
    picture = cv2.imread(str(SHOT))
    assert picture is not None, SHOT
    return cv2.cvtColor(picture, cv2.COLOR_BGR2RGB)


@pytest.mark.skipif(not health()[0], reason="Tesseract is not installed")
def test_max_mana_is_read_off_the_bar_text() -> None:
    frame = shot()
    assert VitalsReader(mp_region=(8, 5, 1088, 12)).read_max_mp(frame) == 295


def test_no_text_reads_as_unknown() -> None:
    frame = shot()
    frame[:] = 0
    assert VitalsReader(mp_region=(8, 5, 1088, 12)).read_max_mp(frame) is None
