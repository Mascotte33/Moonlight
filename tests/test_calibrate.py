"""Unit tests for moonlight.calibration.calibrate.

The core data model, coordinate transforms, and save/load are pure stdlib and
are tested headless (no OpenCV, display, or Tibia window required).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonlight.calibration.calibrate import (
    GAME_GRID_COLS,
    GAME_GRID_ROWS,
    REGIONS,
    CalibrationError,
    CalibrationProfile,
    Region,
    default_profiles_dir,
    normalize_rect,
    region_to_absolute,
    region_to_relative,
)


class TestRegionsConstant:
    def test_all_required_regions_present(self) -> None:
        for name in (
            "hp_bar",
            "mp_bar",
            "battle_list",
            "minimap",
            "floor_indicator",
            "status_icons",
            "game_grid",
        ):
            assert name in REGIONS

    def test_no_duplicates(self) -> None:
        assert len(REGIONS) == len(set(REGIONS))


class TestGameGridConstants:
    def test_grid_is_15x11(self) -> None:
        assert GAME_GRID_COLS == 15
        assert GAME_GRID_ROWS == 11


class TestNormalizeRect:
    def test_normal_drag(self) -> None:
        assert normalize_rect(0, 0, 10, 5) == (0, 0, 10, 5)

    def test_reversed_drag(self) -> None:
        assert normalize_rect(10, 5, 0, 0) == (0, 0, 10, 5)

    def test_zero_dimension(self) -> None:
        assert normalize_rect(5, 5, 5, 5) == (5, 5, 0, 0)


class TestTransforms:
    def test_relative_roundtrip(self) -> None:
        landmark = (100, 200)
        abs_rect = (150, 250, 40, 20)
        rel = region_to_relative(abs_rect, landmark)
        assert rel == (50, 50, 40, 20)
        assert region_to_absolute(rel, landmark) == abs_rect

    def test_negative_offset(self) -> None:
        landmark = (300, 300)
        abs_rect = (100, 50, 10, 10)
        rel = region_to_relative(abs_rect, landmark)
        assert rel == (-200, -250, 10, 10)
        assert region_to_absolute(rel, landmark) == abs_rect


class TestRegion:
    def test_from_tuple(self) -> None:
        r = Region.from_tuple((1, 2, 3, 4))
        assert (r.x, r.y, r.width, r.height) == (1, 2, 3, 4)

    def test_to_tuple_and_list(self) -> None:
        r = Region(1, 2, 3, 4)
        assert r.to_tuple == (1, 2, 3, 4)
        assert r.to_list == [1, 2, 3, 4]

    def test_from_list_validates_length(self) -> None:
        with pytest.raises(CalibrationError):
            Region.from_list([1, 2, 3])

    def test_is_valid(self) -> None:
        assert Region(0, 0, 5, 5).is_valid
        assert not Region(0, 0, 0, 5).is_valid
        assert not Region(0, 0, 5, 0).is_valid

    def test_absolute(self) -> None:
        r = Region(10, 10, 5, 5)
        assert r.absolute((100, 100)) == (110, 110, 5, 5)

    def test_equality_and_hash(self) -> None:
        assert Region(1, 2, 3, 4) == Region(1, 2, 3, 4)
        assert hash(Region(1, 2, 3, 4)) == hash(Region(1, 2, 3, 4))


class TestProfileSerialization:
    def test_to_dict_shape(self) -> None:
        p = CalibrationProfile(landmark=(10, 20))
        p.set_region("hp_bar", Region(1, 2, 3, 4))
        d = p.to_dict()
        assert d["landmark"] == [10, 20]
        regions = d["regions"]
        assert isinstance(regions, dict)
        assert regions["hp_bar"] == [1, 2, 3, 4]

    def test_from_dict_roundtrip(self) -> None:
        p = CalibrationProfile(landmark=(10, 20))
        p.set_region("hp_bar", Region(1, 2, 3, 4))
        restored = CalibrationProfile.from_dict(p.to_dict())
        assert restored.landmark == (10, 20)
        assert restored["hp_bar"] == Region(1, 2, 3, 4)

    def test_window_size_and_ui_scale_roundtrip(self) -> None:
        p = CalibrationProfile(window_size=(2560, 1369), ui_scale=1.5)
        restored = CalibrationProfile.from_dict(p.to_dict())
        assert (restored.window_size, restored.ui_scale) == ((2560, 1369), 1.5)

    def test_old_profile_without_size_or_scale_loads(self) -> None:
        restored = CalibrationProfile.from_dict({"landmark": [0, 0], "regions": {}})
        assert (restored.window_size, restored.ui_scale) == (None, 1.0)

    def test_from_dict_missing_landmark(self) -> None:
        with pytest.raises(CalibrationError):
            CalibrationProfile.from_dict({"regions": {}})

    def test_from_dict_missing_regions(self) -> None:
        with pytest.raises(CalibrationError):
            CalibrationProfile.from_dict({"landmark": [0, 0]})

    def test_from_dict_bad_region_shape(self) -> None:
        with pytest.raises(CalibrationError):
            CalibrationProfile.from_dict(
                {"landmark": [0, 0], "regions": {"hp_bar": [1, 2, 3]}}
            )


class TestProfileValidation:
    def _full_profile(self) -> CalibrationProfile:
        p = CalibrationProfile(landmark=(100, 100))
        for name in REGIONS:
            p.set_region(name, Region(0, 0, 10, 10))
        return p

    def test_valid_profile(self) -> None:
        self._full_profile().validate()  # should not raise

    def test_missing_regions_raises(self) -> None:
        p = CalibrationProfile(landmark=(0, 0))
        p.set_region("hp_bar", Region(0, 0, 5, 5))
        with pytest.raises(CalibrationError):
            p.validate()

    def test_invalid_region_raises(self) -> None:
        p = self._full_profile()
        p.set_region("hp_bar", Region(0, 0, 0, 5))
        with pytest.raises(CalibrationError):
            p.validate()

    def test_set_unknown_region(self) -> None:
        p = CalibrationProfile()
        with pytest.raises(CalibrationError):
            p.set_region("bogus", Region(0, 0, 5, 5))

    def test_get_missing_region(self) -> None:
        p = CalibrationProfile()
        with pytest.raises(CalibrationError):
            p.get_region("hp_bar")

    def test_contains_and_iter(self) -> None:
        p = self._full_profile()
        assert "hp_bar" in p
        assert set(iter(p)) == set(REGIONS)


class TestProfileSaveLoad:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        p = CalibrationProfile(landmark=(100, 100))
        p.set_region("hp_bar", Region(5, 5, 10, 10))
        out = p.save(tmp_path / "profiles" / "test.json")
        loaded = CalibrationProfile.load(out)
        assert loaded.landmark == (100, 100)
        assert loaded["hp_bar"] == Region(5, 5, 10, 10)
        # Saved JSON must match the documented schema.
        raw = json.loads(out.read_text(encoding="utf-8"))
        assert raw["landmark"] == [100, 100]
        assert raw["regions"]["hp_bar"] == [5, 5, 10, 10]

    def test_load_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(CalibrationError):
            CalibrationProfile.load(tmp_path / "nope.json")


class TestProfileAccess:
    def test_region_absolute(self) -> None:
        p = CalibrationProfile(landmark=(0, 0))
        p.set_region("hp_bar", Region(5, 5, 10, 10))
        # At a different runtime landmark:
        assert p.region_absolute("hp_bar", (200, 300)) == (205, 305, 10, 10)


class TestProfilesDir:
    def test_default_profiles_dir_exists_as_path(self) -> None:
        d = default_profiles_dir()
        assert d.name == "profiles"
