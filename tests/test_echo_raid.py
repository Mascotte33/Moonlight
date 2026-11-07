"""The echo raid swirl scan on the wiki's 10 animation frames."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image, ImageSequence

from moonlight.perception.echo_raid import find_echo_raids

GIF = Path(__file__).parent / "echo_raid_trigger.gif"
#: The owner's game grid (x, y, w, h): 15x11 tiles of about 37.5 px.
GRID = (6, 61, 562, 412)


def _frames() -> list[np.ndarray]:
    """The trigger's frames as RGBA, 64x64 px (2x2 tiles at 32 px), like the capture."""
    with Image.open(GIF) as gif:
        return [np.array(f.convert("RGBA")) for f in ImageSequence.Iterator(gif)]


def _floor() -> np.ndarray:
    """A grey stone floor with noise, the size of a 1034x1008 client."""
    rng = np.random.default_rng(1)
    return rng.integers(60, 110, (1008, 1034, 3), dtype=np.uint8)


def _draw(frame: np.ndarray, sprite: np.ndarray, slot: tuple[int, int]) -> None:
    """Draw *sprite* the client's way: 2x2 tiles up-left of its *slot*."""
    gx, gy, gw, gh = GRID
    tw, th = gw / 15, gh / 11
    size = (round(2 * tw), round(2 * th))
    scaled = cv2.resize(sprite, size, interpolation=cv2.INTER_NEAREST)
    x = gx + round((slot[0] + 1) * tw) - size[0]
    y = gy + round((slot[1] + 1) * th) - size[1]
    area = frame[y : y + size[1], x : x + size[0]]
    alpha = scaled[..., 3:] > 0
    area[:] = np.where(alpha, scaled[..., :3], area)


@pytest.mark.parametrize("index", range(10))
def test_every_frame_is_found_on_its_tile(index: int) -> None:
    frame = _floor()
    _draw(frame, _frames()[index], (10, 3))
    assert find_echo_raids(frame, GRID) == [(10, 3)]


def test_two_triggers_are_both_found() -> None:
    frame = _floor()
    sprites = _frames()
    _draw(frame, sprites[0], (3, 8))
    _draw(frame, sprites[5], (12, 2))
    assert find_echo_raids(frame, GRID) == [(3, 8), (12, 2)]


def test_plain_floor_fire_and_health_bars_are_no_trigger() -> None:
    frame = _floor()
    gx, gy = GRID[:2]
    frame[gy + 100 : gy + 140, gx + 100 : gx + 140] = (255, 140, 0)  # fire / lava orange
    frame[gy + 200 : gy + 202, gx + 300 : gx + 329] = (192, 0, 0)  # a red health bar
    frame[gy + 250 : gy + 290, gx + 400 : gx + 440] = (250, 250, 250)  # white light
    assert find_echo_raids(frame, GRID) == []


def test_a_few_salmon_pixels_are_no_trigger() -> None:
    frame = _floor()
    gx, gy = GRID[:2]
    frame[gy + 50 : gy + 55, gx + 50 : gx + 55] = (255, 116, 80)  # a spell spark
    assert find_echo_raids(frame, GRID) == []


def test_a_blue_swirl_is_no_trigger() -> None:
    # bot.log 2026-11-01: blue rune rings and fire basins read as echo raids (BGR bounds
    # on the capture's RGB frames); the red trigger was never found.
    frame = _floor()
    gx, gy = GRID[:2]
    frame[gy + 50 : gy + 90, gx + 50 : gx + 90] = (60, 116, 255)
    assert find_echo_raids(frame, GRID) == []


def test_the_live_client_trigger_is_found_and_crates_are_not() -> None:
    # Owner's client 2026-11-01: the trigger top-left, wooden crates and fire basins around.
    frame = np.array(Image.open(Path(__file__).parent / "echo_raid_live.png").convert("RGB"))
    assert find_echo_raids(frame, (0, 0, 562, 412)) == [(4, 1)]
