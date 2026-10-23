"""WorldMap finds a radar crop on a synthetic stitched map."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from moonlight.perception.worldmap import WorldMap


def _map(tmp_path: Path) -> np.ndarray:
    rng = np.random.RandomState(3)
    tile = rng.randint(0, 255, (256, 256, 3), dtype=np.uint8)
    cv2.imwrite(str(tmp_path / "32000_31000_7.png"), cv2.cvtColor(tile, cv2.COLOR_RGB2BGR))
    return tile


def _radar(tile: np.ndarray, x: int, y: int, dy: int = 0) -> np.ndarray:
    """Tiles from (x, y) drawn at 2 px per tile, *dy* px down, at radar size."""
    part = tile[y : y + 56, x : x + 53]
    return np.repeat(np.repeat(part, 2, axis=0), 2, axis=1)[dy : dy + 109, :106]


@pytest.mark.parametrize("dy", [0, 1])
def test_radar_center_is_found(tmp_path: Path, dy: int) -> None:
    radar = _radar(_map(tmp_path), 40, 60, dy)
    world = WorldMap(tmp_path)
    # The radar center (53, 54) px is tile (26, 27) of the crop either way.
    assert world.match(radar, 7) == (32000 + 40 + 26, 31000 + 60 + 27)
    assert world.match(radar, 7, near=(32070, 31080)) == (32066, 31087)
    assert world.zoom == 2


def test_radar_at_one_px_per_tile_is_found(tmp_path: Path) -> None:
    radar = _map(tmp_path)[60:169, 40:146]
    world = WorldMap(tmp_path)
    assert world.match(radar, 7) == (32000 + 40 + 53, 31000 + 60 + 54)
    assert world.zoom == 1


def test_unknown_floor_or_bad_radar_is_none(tmp_path: Path) -> None:
    _map(tmp_path)
    world = WorldMap(tmp_path, full_search_every=0.0)
    noise = np.random.RandomState(9).randint(0, 255, (109, 106, 3), dtype=np.uint8)
    assert world.match(noise, 7) is None
    assert world.match(noise, 6) is None
    assert WorldMap(tmp_path / "missing").empty
