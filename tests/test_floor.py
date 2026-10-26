"""Unit tests for moonlight.perception.floor.

All tests are offline and use SYNTHETIC numpy frames and templates -- no
live client, no golden screenshots, and no files on disk (except an
explicit tmp-directory test for ``TemplateSet.load``). The golden-image
tests live in ``tests/perf/test_floor_golden.py`` and skip until real
frames are provided (T08).

The synthetic templates mimic the real layout from docs/ui_layout.md #5:
a small indicator icon per floor level (16 levels, 0..15) that is
template-matched against the calibrated floor-indicator region. Each level
gets a distinct bright 3x3 block at a unique grid position inside a dark
12x12 template, so the correct level is identifiable without ambiguity.
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from moonlight.calibration.calibrate import CalibrationProfile, Region
from moonlight.perception.floor import (
    FLOOR_LEVELS,
    GROUND_FLOOR,
    MAX_FLOOR,
    MIN_FLOOR,
    FloorReader,
    TemplateSet,
)

TEMPLATE_SIZE = 12
BLOCK_SIZE = 3

#: Frame coordinates of the synthetic floor-indicator region.
REGION_X, REGION_Y = 10, 20


def block_position(level: int) -> tuple[int, int]:
    """Grid cell (row, col) of the bright block for *level* (0..15)."""
    return (level // 4) * BLOCK_SIZE, (level % 4) * BLOCK_SIZE


def make_template(level: int) -> np.ndarray:
    """A distinct 12x12 grayscale template for floor *level*."""
    image = np.zeros((TEMPLATE_SIZE, TEMPLATE_SIZE), dtype=np.uint8)
    r, c = block_position(level)
    image[r : r + BLOCK_SIZE, c : c + BLOCK_SIZE] = 220
    return image


def make_templates() -> TemplateSet:
    """A TemplateSet with one template per floor level (0..15)."""
    return TemplateSet({level: make_template(level) for level in FLOOR_LEVELS})


def make_frame(
    level: int | None = None, noise: int = 0
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Build a synthetic frame whose indicator region shows floor *level*.

    With *level=None* the region stays blank (no recognizable icon).
    *noise* adds uniform integer noise in [0, noise) to simulate capture
    artifacts.

    Returns ``(frame, region)`` where *region* covers exactly the widget.
    """
    region = (REGION_X, REGION_Y, TEMPLATE_SIZE, TEMPLATE_SIZE)
    frame = np.zeros((60, 80, 3), dtype=np.uint8)
    frame[:] = (5, 5, 5)  # outside the widget
    x, y, w, h = region
    if level is not None:
        icon = make_template(level)
        frame[y : y + h, x : x + w, 0] = icon
        frame[y : y + h, x : x + w, 1] = icon
        frame[y : y + h, x : x + w, 2] = icon
    if noise:
        rng = np.random.RandomState(42)
        patch = frame[y : y + h, x : x + w]
        patch += rng.randint(0, noise, size=patch.shape, dtype=np.uint8)
    return frame, region


class TestConstants:
    """Floor level conventions from docs/ui_layout.md #5."""

    def test_ground_floor_is_7(self) -> None:
        assert GROUND_FLOOR == 7

    def test_level_range(self) -> None:
        assert MIN_FLOOR == 0
        assert MAX_FLOOR == 15
        assert FLOOR_LEVELS == tuple(range(16))


class TestTemplateSet:
    """TemplateSet validation, loading, and matching."""

    def test_levels_sorted(self) -> None:
        ts = TemplateSet({3: make_template(3), 1: make_template(1), 9: make_template(9)})
        assert ts.levels() == (1, 3, 9)

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one template"):
            TemplateSet({})

    def test_invalid_level_raises(self) -> None:
        with pytest.raises(ValueError, match="must be 0\\.\\.15"):
            TemplateSet({-1: make_template(0)})
        with pytest.raises(ValueError, match="must be 0\\.\\.15"):
            TemplateSet({16: make_template(0)})

    def test_non_gray_raises(self) -> None:
        # A 3-D (RGB-style) image is not a valid grayscale template.
        with pytest.raises(ValueError, match="2-D grayscale"):
            TemplateSet({0: make_template(0).reshape(TEMPLATE_SIZE, TEMPLATE_SIZE, 1)})

    def test_load_missing_directory_returns_none(self) -> None:
        assert TemplateSet.load("no-such-dir-xyz") is None

    def test_load_from_disk(self, tmp_path: Path) -> None:
        import cv2

        for level in (0, 7, 15):
            ok = cv2.imwrite(str(tmp_path / f"{level}.png"), make_template(level))
            assert ok, f"cv2.imwrite failed for level {level}"
        ts = TemplateSet.load(tmp_path)
        assert ts is not None
        assert ts.levels() == (0, 7, 15)

    def test_match_returns_best_level(self) -> None:
        ts = make_templates()
        icon = make_template(7)
        result = ts.match(icon, threshold=0.5)
        assert result is not None
        level, score = result
        assert level == 7
        assert score > 0.9

    def test_match_rejects_blank_region(self) -> None:
        ts = make_templates()
        blank = np.zeros((TEMPLATE_SIZE, TEMPLATE_SIZE), dtype=np.uint8)
        assert ts.match(blank, threshold=0.8) is None

    def test_match_skips_oversized_template(self) -> None:
        big = make_template(0)
        roi = np.zeros((5, 5), dtype=np.uint8)
        ts = TemplateSet({0: big})
        assert ts.match(roi, threshold=0.0) is None

    def test_match_threshold_respected(self) -> None:
        ts = TemplateSet({7: make_template(7)})
        # Same grid cell but extra bright pixels elsewhere: still recognizable
        # at a loose threshold, rejected by a strict one (scale invariance of
        # TM_CCOEFF_NORMED means intensity changes alone won't lower the score).
        icon = make_template(7).copy()
        icon[0:3, 0:3] = 220
        high = ts.match(icon, threshold=0.5)
        low = ts.match(icon, threshold=0.999)
        assert high is not None
        assert high[1] < 0.999
        assert low is None
        # An unrelated icon does not match even with a loose threshold.
        assert ts.match(make_template(0), threshold=0.1) is None


class TestFloorReader:
    """FloorReader read_floor behavior with synthetic frames."""

    @staticmethod
    def _reader(**kwargs: object) -> FloorReader:
        return FloorReader(template_set=make_templates(), **kwargs)  # type: ignore[arg-type]

    def test_all_sixteen_levels_detected(self) -> None:
        reader = self._reader()
        for level in FLOOR_LEVELS:
            frame, region = make_frame(level)
            assert reader.read_floor(frame, region) == level, f"level {level} misread"

    def test_noisy_frame_still_detected(self) -> None:
        # Mild capture noise: still above the 0.80 default threshold.
        reader = self._reader()
        for level in (0, 3, 7, 11, 15):
            frame, region = make_frame(level, noise=10)
            assert reader.read_floor(frame, region) == level

    def test_blank_region_returns_none(self) -> None:
        # No recognizable icon: fail closed with None, never a guess.
        reader = self._reader()
        frame, region = make_frame(level=None)
        assert reader.read_floor(frame, region) is None

    def test_missing_templates_returns_none(self) -> None:
        # No templates and no slider handle: fail closed.
        reader = FloorReader(template_dir="no-such-dir-xyz")
        frame, region = make_frame(7)
        assert reader.read_floor(frame, region) is None

    @pytest.mark.parametrize("level", [0, 7, 9, 15])
    def test_without_templates_the_slider_handle_gives_the_floor(self, level: int) -> None:
        # 22x72 indicator: track ~70 gray, a 6 px handle centred on row 5 + 4 * level.
        frame = np.full((100, 60, 3), 70, np.uint8)
        top = 10 + 5 + 4 * level - 2
        frame[top : top + 6, 10 + 16 : 10 + 20] = 120
        reader = FloorReader(template_dir="no-such-dir-xyz")
        assert reader.read_floor(frame, (10, 10, 22, 72)) == level

    def test_no_region_returns_none(self) -> None:
        frame, _ = make_frame(7)
        assert FloorReader(template_set=make_templates()).read_floor(frame) is None

    def test_region_outside_frame_returns_none(self) -> None:
        frame, _ = make_frame(7)
        h, w = frame.shape[:2]
        reader = self._reader()
        assert reader.read_floor(frame, (w + 5, h + 5, TEMPLATE_SIZE, TEMPLATE_SIZE)) is None

    def test_ui_scale_reads_an_enlarged_indicator(self) -> None:
        # The client at UI scale 1.5: templates are enlarged to match.
        profile = CalibrationProfile(ui_scale=1.5)
        profile.set_region("floor_indicator", Region(15, 30, 18, 18))
        reader = FloorReader(profile, template_set=make_templates())
        for level in FLOOR_LEVELS:
            frame, _ = make_frame(level)
            big = cv2.resize(frame, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_NEAREST)
            assert reader.read_floor(big) == level

    def test_region_clipped_to_frame(self) -> None:
        # Region extends past the frame edge: the visible icon still matches.
        reader = self._reader()
        frame, (x, y, w, h) = make_frame(7)
        clipped = (x - 3, y - 2, w + 4, h + 3)
        assert reader.read_floor(frame, clipped) == 7

    def test_default_region_from_profile(self) -> None:
        reader = FloorReader(template_set=make_templates())  # no profile
        assert reader.floor_region is None
        frame, region = make_frame(7)
        profile = CalibrationProfile(landmark=(0, 0))
        profile.set_region("floor_indicator", Region(*region))
        reader = FloorReader(profile, template_set=make_templates())
        assert reader.read_floor(frame) == 7

    def test_explicit_region_overrides_profile(self) -> None:
        profile = CalibrationProfile(landmark=(0, 0))
        profile.set_region("floor_indicator", Region(0, 0, TEMPLATE_SIZE, TEMPLATE_SIZE))
        reader = FloorReader(profile, floor_region=(1, 2, 3, 4), template_set=make_templates())
        assert reader.floor_region == (1, 2, 3, 4)

    def test_invalid_region_raises(self) -> None:
        with pytest.raises(ValueError):
            FloorReader(floor_region=(1, 2, 3))  # type: ignore[arg-type]

    def test_custom_threshold(self) -> None:
        # Noisy icon scores ~0.96: clear margins either side, unlike a perfect
        # match (~1.0 +/- float rounding, which differs across OpenCV builds).
        frame, region = make_frame(7, noise=40)
        strict = FloorReader(template_set=make_templates(), match_threshold=0.99)
        assert strict.read_floor(frame, region) is None
        loose = FloorReader(template_set=make_templates(), match_threshold=0.5)
        assert loose.read_floor(frame, region) == 7

    def test_template_dir_lazy_load(self, tmp_path: Path) -> None:
        # Templates loaded lazily from disk on first read (no injection).
        import cv2

        for level in FLOOR_LEVELS:
            assert cv2.imwrite(str(tmp_path / f"{level}.png"), make_template(level))
        reader = FloorReader(template_dir=tmp_path)
        frame, region = make_frame(15)
        assert reader.read_floor(frame, region) == 15


class TestPerformance:
    """Per-read latency on a realistic frame (budget < 10 ms, T30)."""

    def test_read_under_10_ms(self) -> None:
        reader = FloorReader(template_set=make_templates())
        h, w = 1080, 1920
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:] = (5, 5, 5)
        region = (1200, 300, TEMPLATE_SIZE, TEMPLATE_SIZE)
        x, y, rw, rh = region
        icon = make_template(7)
        frame[y : y + rh, x : x + rw, 0] = icon
        frame[y : y + rh, x : x + rw, 1] = icon
        frame[y : y + rh, x : x + rw, 2] = icon

        # Warm up, then time several reads.
        for _ in range(3):
            reader.read_floor(frame, region)
        start = time.perf_counter()
        iterations = 100
        for _ in range(iterations):
            reader.read_floor(frame, region)
        elapsed_ms = (time.perf_counter() - start) * 1000.0 / iterations
        assert elapsed_ms < 10.0, f"read_floor took {elapsed_ms:.3f} ms (budget 10 ms)"
        assert reader.read_floor(frame, region) == 7
