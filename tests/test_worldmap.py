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


# doc: docs/prs/client-hotkeys.md#sparse-radar
def test_a_near_search_accepts_a_looser_score(tmp_path: Path) -> None:
    radar = _radar(_map(tmp_path), 40, 60).astype(np.int32)
    noise = np.random.RandomState(5).randint(-40, 40, radar.shape)
    radar = np.clip(radar + noise, 0, 255).astype(np.uint8)  # scores above 0
    world = WorldMap(tmp_path, max_score=0.0, near_max_score=0.2)
    assert world.match(radar, 7) is None
    assert world.match(radar, 7, near=(32070, 31080)) == (32066, 31087)


# doc: docs/prs/client-hotkeys.md#distinct-full-match
def test_a_whole_floor_search_accepts_a_looser_score_only_at_a_distinct_spot(
    tmp_path: Path,
) -> None:
    rng = np.random.RandomState(4)
    tile = np.zeros((256, 256, 3), np.uint8)  # mostly black, like an upper floor
    for _ in range(30):
        x, y, size = rng.randint(0, 240), rng.randint(0, 240), rng.randint(4, 12)
        tile[y : y + size, x : x + size] = rng.randint(60, 255, 3)
    cv2.imwrite(str(tmp_path / "32000_31000_7.png"), cv2.cvtColor(tile, cv2.COLOR_RGB2BGR))
    radar = _radar(tile, 40, 60)
    world = WorldMap(tmp_path, max_score=-1.0, full_search_every=0.0)  # only "distinct" passes
    assert world.match(radar, 7) == (32066, 31087)
    # The same tile twice: two equally good spots, so neither is trusted.
    cv2.imwrite(str(tmp_path / "32256_31000_7.png"), cv2.cvtColor(tile, cv2.COLOR_RGB2BGR))
    assert WorldMap(tmp_path, max_score=-1.0).match(radar, 7) is None


# doc: docs/prs/client-hotkeys.md#lit-pixels-only
def test_the_player_cross_and_black_radar_parts_are_not_compared(tmp_path: Path) -> None:
    tile = np.zeros((256, 256, 3), np.uint8)  # an upper floor: two trees and two roofs
    tile[80:90, 60:70] = (0, 100, 0)
    tile[70:80, 90:100] = (0, 100, 0)
    tile[85:92, 82:95] = (153, 102, 51)  # the roof the character stands on
    tile[90:95, 100:108] = (153, 102, 51)  # a roof the radar does not draw
    cv2.imwrite(str(tmp_path / "32000_31000_7.png"), cv2.cvtColor(tile, cv2.COLOR_RGB2BGR))
    radar = tile[60:169, 40:146].copy()  # 1 px per sqm, center (93, 114)
    radar[52:58, 50:56] = 255  # the player cross
    radar[30:35, 60:68] = 0  # the undrawn roof
    world = WorldMap(tmp_path, full_search_every=0.0)
    assert world.match(radar, 7) == (32093, 31114)
    assert world.match(radar, 7, near=(32090, 31110)) == (32093, 31114)
