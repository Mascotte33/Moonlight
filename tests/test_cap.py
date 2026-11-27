"""Offline tests for the side panel Cap reader."""

# doc: docs/prs/potion-count.md#cap-reader

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
import pytest

from moonlight.perception.cap import CAP_DIGITS, CapReader, read_number
from moonlight.refill.refill import LAST_READ, RefillConfig, supplies_low

#: A real crop of the side panel: Soul 100, Cap 985 (the Windows client).
PANEL = Path(__file__).parent / "cap_panel.png"


@pytest.fixture(autouse=True)
def _windows_pictures(monkeypatch: pytest.MonkeyPatch) -> None:
    """The panel is a Windows grab: read it with the Windows label, on any OS."""
    monkeypatch.setattr("moonlight.perception.pictures.sys.platform", "win32")


def panel_in_frame(x: int, y: int) -> np.ndarray:
    bgr = cv2.imread(str(PANEL))
    assert bgr is not None
    panel = bgr[:, :, ::-1]
    frame = np.zeros((400, 600, 3), np.uint8)
    frame[y : y + panel.shape[0], x : x + panel.shape[1]] = panel
    return frame


def test_reads_the_cap_and_finds_a_moved_panel() -> None:
    frames = [panel_in_frame(400, 300)]
    reader = CapReader(lambda: frames[0])
    assert reader.cap() == 985
    frames[0] = panel_in_frame(10, 20)
    assert reader.cap() == 985
    frames[0] = np.zeros((400, 600, 3), np.uint8)
    assert reader.cap() is None


# doc: docs/prs/potion-count.md#last-read
def test_check_logs_each_number_and_keeps_the_last_read(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    frames = [panel_in_frame(0, 0)]
    low = supplies_low(RefillConfig(cap_below=1000), lambda: frames[0])
    assert low is not None and low()
    assert "check: cap 985 < 1000 | refill" in caplog.text
    n, pic = LAST_READ["refill.cap_below"]
    assert n == 985 and pic is not None and read_number(pic) == 985
    frames[0] = np.zeros((400, 600, 3), np.uint8)
    assert not low()  # an unknown cap never sends the bot to town
    assert "check: cap ? | continue" in caplog.text
    assert LAST_READ["refill.cap_below"] == (None, None)
    assert supplies_low(RefillConfig(), lambda: frames[0]) is None
    frames[0] = panel_in_frame(0, 0)
    high = supplies_low(RefillConfig(cap_below=900), lambda: frames[0])
    assert high is not None and not high()
    assert "check: cap 985 >= 900 | continue" in caplog.text


def draw(*shapes: str) -> np.ndarray:
    """RGB pixels with *shapes* (CAP_DIGITS keys) side by side, a column apart."""
    cols = []
    for shape in shapes:
        rows = shape.split("/")
        cols.append(np.array([[c == "#" for c in row] for row in rows]))
        cols.append(np.zeros((len(rows), 1), bool))
    lit = np.hstack(cols)
    return np.where(lit[:, :, None], 220, 30).astype(np.uint8).repeat(3, axis=2)


def test_digits_match_a_known_shape_or_one_clearly_nearest() -> None:
    shape = {d: g for g, d in CAP_DIGITS.items()}
    assert read_number(draw(shape["1"], shape["5"], shape["3"], shape["0"])) == 1530
    assert read_number(draw(shape["3"], shape["04"])) == 304  # touching pair
    three = shape["3"]
    off = three.index("/.") + 1
    near = three[:off] + "#" + three[off + 1 :]  # one pixel off a 3
    assert near not in CAP_DIGITS
    assert read_number(draw(shape["1"], near)) == 13
    between = "####./#####/##.##/##.##/#####/#..##/#####/####."  # half 0, half 9
    assert between not in CAP_DIGITS
    assert read_number(draw(shape["1"], between)) is None


def test_an_unlisted_touching_pair_is_split_where_both_halves_read() -> None:
    # Linux client 2026-11-21: "375" with the 7 and the 5 joined by one pixel.
    seventy_five = (
        "#####..#####/######.#####/...##..##.../...##..####./"
        "..##...#####/..##......##/.##...######/.##...#####."
    )
    assert seventy_five not in CAP_DIGITS
    three = "#####/#####/...##/.###./.####/...##/#####/#####"
    assert read_number(draw(three, seventy_five)) == 375


def test_three_unlisted_joined_digits_are_split() -> None:
    # Linux client 2026-11-27: cap 480 as one shape on a newly logged-in character.
    four_eighty = (
        "..###..####..####./..###.#####..#####/.####.##.##.##..##/##.##..####.##..##/"
        "#####.#####.##..##/########..####..##/...##.##.###.#####/...##..####..####."
    )
    assert four_eighty not in CAP_DIGITS
    assert read_number(draw(four_eighty)) == 480


def test_three_joined_digits_read_as_one_listed_shape() -> None:
    # Linux client 2026-11-26: cap 484 drawn as one shape; the bot would not start.
    shape = {d: g for g, d in CAP_DIGITS.items()}
    assert read_number(draw(shape["484"])) == 484
    assert read_number(draw(shape["487"])) == 487


def test_a_zero_seen_on_2026_11_26_reads() -> None:
    zero = ".####./.#####/##..##/##..##/##..##/##..##/.#####/.####."
    assert read_number(draw(zero)) == 0
