"""Unit tests for moonlight.perception.vitals.

All tests are offline and use SYNTHETIC numpy bar images -- no live client,
no OpenCV, and no golden screenshots are required. The golden-image tests
live in ``tests/perf/test_vitals_golden.py`` and skip until real frames are
provided (T08).
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from moonlight.calibration.calibrate import CalibrationProfile, Region
from moonlight.perception.vitals import (
    BAR_BACKGROUND_RGB,
    HP_FILL_RGB,
    MP_FILL_RGB,
    VitalsReader,
    bar_percent,
)

WIDTH = 100
HEIGHT = 23
FULL_REGION = (0, 0, WIDTH, HEIGHT)


def make_bar_frame(
    fill_percent: float,
    fill_rgb: tuple[int, int, int] = HP_FILL_RGB,
    bg_rgb: tuple[int, int, int] = BAR_BACKGROUND_RGB,
    width: int = WIDTH,
    height: int = HEIGHT,
    offset: tuple[int, int] = (0, 0),
) -> np.ndarray:
    """Build a synthetic frame containing one horizontal bar.

    The bar fills its region from the left edge by ``fill_percent`` and is
    placed at exactly ``(offset[0], offset[1])`` in the frame. The rest of
    the frame is a clearly-different colour so only the bar colours appear
    inside a correctly calibrated region.
    """
    ox, oy = offset
    frame = np.zeros((height + oy + 4, width + ox + 4, 3), dtype=np.uint8)
    frame[:] = (10, 20, 30)
    bar = frame[oy : oy + height, ox : ox + width]
    bar[:] = bg_rgb
    filled = int(round(width * fill_percent / 100.0))
    if filled > 0:
        bar[:, :filled] = fill_rgb
    return frame


def bar_region(offset: tuple[int, int] = (0, 0)) -> tuple[int, int, int, int]:
    return (offset[0], offset[1], WIDTH, HEIGHT)


class TestBarPercentHP:
    """HP fill detection on synthetic bars."""

    def test_full_bar(self) -> None:
        frame = make_bar_frame(100.0)
        assert bar_percent(frame, FULL_REGION, HP_FILL_RGB) == pytest.approx(100.0)

    def test_empty_bar(self) -> None:
        frame = make_bar_frame(0.0)
        assert bar_percent(frame, FULL_REGION, HP_FILL_RGB) == pytest.approx(0.0)

    @pytest.mark.parametrize("pct", [10.0, 25.0, 33.0, 50.0, 75.0, 90.0])
    def test_partial_fills(self, pct: float) -> None:
        frame = make_bar_frame(pct)
        assert bar_percent(frame, FULL_REGION, HP_FILL_RGB) == pytest.approx(pct)

    def test_mp_fill_uses_mp_colour(self) -> None:
        # An MP-coloured bar should be ~0 for HP detection and ~100 for MP.
        frame = make_bar_frame(80.0, fill_rgb=MP_FILL_RGB)
        assert bar_percent(frame, FULL_REGION, HP_FILL_RGB) == pytest.approx(0.0, abs=1.0)
        assert bar_percent(frame, FULL_REGION, MP_FILL_RGB) == pytest.approx(80.0, abs=1.0)


class TestBarPercentMP:
    """MP fill detection on synthetic bars."""

    @pytest.mark.parametrize("pct", [0.0, 40.0, 65.0, 100.0])
    def test_mp_percentages(self, pct: float) -> None:
        frame = make_bar_frame(pct, fill_rgb=MP_FILL_RGB)
        assert bar_percent(
            frame, FULL_REGION, MP_FILL_RGB
        ) == pytest.approx(pct, abs=0.5)


class TestTolerance:
    """Per-channel compression-artifact tolerance (default +/-20)."""

    def _perturbed_fill(self, delta: int) -> tuple[int, int, int]:
        return tuple(max(0, min(255, c + delta)) for c in HP_FILL_RGB)  # type: ignore[return-value]

    def test_within_tolerance_detected(self) -> None:
        # Perturb each channel by +15 (inside +/-20): still detected.
        fill = self._perturbed_fill(15)
        frame = make_bar_frame(60.0, fill_rgb=fill)
        assert bar_percent(frame, FULL_REGION, HP_FILL_RGB) == pytest.approx(60.0, abs=1.0)

    def test_negative_perturbation_detected(self) -> None:
        fill = self._perturbed_fill(-18)
        frame = make_bar_frame(45.0, fill_rgb=fill)
        assert bar_percent(frame, FULL_REGION, HP_FILL_RGB) == pytest.approx(45.0, abs=1.0)

    def test_at_tolerance_boundary_detected(self) -> None:
        # Exactly +20 is still within tolerance (<= 20).
        fill = self._perturbed_fill(20)
        frame = make_bar_frame(50.0, fill_rgb=fill)
        assert bar_percent(frame, FULL_REGION, HP_FILL_RGB) == pytest.approx(50.0, abs=1.0)

    def test_beyond_tolerance_not_detected(self) -> None:
        # +25 on all channels is outside tolerance: bar not recognised.
        fill = self._perturbed_fill(25)
        frame = make_bar_frame(50.0, fill_rgb=fill)
        assert bar_percent(frame, FULL_REGION, HP_FILL_RGB) == pytest.approx(0.0, abs=1.0)

    def test_custom_tolerance_used(self) -> None:
        fill = self._perturbed_fill(25)
        frame = make_bar_frame(50.0, fill_rgb=fill)
        # A looser tolerance (30) recognises the bar again.
        assert bar_percent(
            frame, FULL_REGION, HP_FILL_RGB, tolerance=30
        ) == pytest.approx(50.0, abs=1.0)


class TestEdgeCases:
    """Empty/invalid regions, out-of-bounds, and non-bar content."""

    def test_zero_width_region(self) -> None:
        frame = make_bar_frame(50.0)
        assert bar_percent(frame, (0, 0, 0, HEIGHT), HP_FILL_RGB) == 0.0

    def test_zero_height_region(self) -> None:
        frame = make_bar_frame(50.0)
        assert bar_percent(frame, (0, 0, WIDTH, 0), HP_FILL_RGB) == 0.0

    def test_negative_region(self) -> None:
        # Bar placed away from the origin; a negative-coord region clips to
        # (0, 0, 20, 20), which contains no bar -> 0 (and no crash).
        frame = make_bar_frame(50.0, offset=(60, 40))
        region = (-10, -10, 20, 20)
        assert bar_percent(frame, region, HP_FILL_RGB) == 0.0

    def test_region_entirely_outside_frame(self) -> None:
        frame = make_bar_frame(50.0)
        h, w = frame.shape[:2]
        assert bar_percent(frame, (w + 5, h + 5, WIDTH, HEIGHT), HP_FILL_RGB) == 0.0

    def test_region_partially_outside_is_clipped(self) -> None:
        # A region wider than the frame must clip, not crash. The whole
        # frame is HP fill colour, so the clipped read is still 100%
        # (denominator = clipped width = frame width = fill width).
        frame = np.full((HEIGHT, WIDTH, 3), HP_FILL_RGB, dtype=np.uint8)
        region = (0, 0, WIDTH + 50, HEIGHT)
        assert bar_percent(frame, region, HP_FILL_RGB) == pytest.approx(100.0, abs=0.1)

    def test_non_bar_content_reads_zero(self) -> None:
        # A region filled with a colour that is neither fill nor background.
        frame = np.full((HEIGHT, WIDTH, 3), (200, 10, 10), dtype=np.uint8)
        assert bar_percent(frame, FULL_REGION, HP_FILL_RGB) == 0.0

    def test_result_clamped_to_100(self) -> None:
        # Even if noise made every pixel match, never exceed 100.
        frame = np.full((HEIGHT, WIDTH, 3), HP_FILL_RGB, dtype=np.uint8)
        assert bar_percent(frame, FULL_REGION, HP_FILL_RGB) <= 100.0


class TestOffsetRegion:
    """Bars not anchored at the frame origin."""

    def test_bar_at_offset(self) -> None:
        offset = (37, 129)
        frame = make_bar_frame(42.0, offset=offset)
        region = bar_region(offset)
        assert bar_percent(frame, region, HP_FILL_RGB) == pytest.approx(42.0, abs=0.5)


class TestVitalsReader:
    """VitalsReader API, constructor, and defaults."""

    def test_explicit_region_hp(self) -> None:
        reader = VitalsReader(hp_region=FULL_REGION, mp_region=FULL_REGION)
        frame = make_bar_frame(66.0)
        assert reader.read_hp(frame, FULL_REGION) == pytest.approx(66.0, abs=0.5)

    @pytest.mark.parametrize("rgb", [(181, 45, 45), (90, 0, 0), (0, 181, 0), (190, 145, 8)])
    def test_every_hp_stage_is_read(self, rgb: tuple[int, int, int]) -> None:
        # (181, 45, 45) is the red stage the old colour list missed.
        reader = VitalsReader(hp_region=FULL_REGION)
        assert reader.read_hp(make_bar_frame(20.0, fill_rgb=rgb)) == pytest.approx(20.0, abs=0.5)

    def test_mp_reader_uses_mp_colour(self) -> None:
        reader = VitalsReader(mp_region=FULL_REGION)
        frame = make_bar_frame(73.0, fill_rgb=MP_FILL_RGB)
        assert reader.read_mp(frame, FULL_REGION) == pytest.approx(73.0, abs=0.5)

    def test_default_region_from_constructor(self) -> None:
        reader = VitalsReader(hp_region=FULL_REGION)
        frame = make_bar_frame(55.0)
        # No explicit region argument: falls back to the stored region.
        assert reader.read_hp(frame) == pytest.approx(55.0, abs=0.5)

    def test_missing_region_returns_zero(self) -> None:
        reader = VitalsReader()
        frame = make_bar_frame(55.0)
        assert reader.read_hp(frame) == 0.0
        assert reader.read_mp(frame) == 0.0

    def test_read_vitals(self) -> None:
        reader = VitalsReader(hp_region=FULL_REGION, mp_region=FULL_REGION)
        # HP frame.
        hp_frame = make_bar_frame(81.0)
        hp, mp = reader.read_vitals(hp_frame)
        assert hp == pytest.approx(81.0, abs=0.5)
        assert mp == pytest.approx(0.0, abs=1.0)

    def test_constructor_from_profile(self) -> None:
        profile = CalibrationProfile(landmark=(100, 100))
        profile.set_region("hp_bar", Region(5, 10, WIDTH, HEIGHT))
        profile.set_region("mp_bar", Region(5, 33, WIDTH, HEIGHT))
        reader = VitalsReader(profile)

        # Build a frame with both bars at their profile offsets (absolute).
        frame = np.zeros((80, WIDTH + 10, 3), dtype=np.uint8)
        frame[:] = (10, 20, 30)
        hp_crop = frame[10 : 10 + HEIGHT, 5 : 5 + WIDTH]
        hp_crop[:] = BAR_BACKGROUND_RGB
        hp_crop[:, : int(WIDTH * 0.7)] = HP_FILL_RGB
        mp_crop = frame[33 : 33 + HEIGHT, 5 : 5 + WIDTH]
        mp_crop[:] = BAR_BACKGROUND_RGB
        mp_crop[:, : int(WIDTH * 0.3)] = MP_FILL_RGB

        assert reader.read_hp(frame) == pytest.approx(70.0, abs=1.0)
        assert reader.read_mp(frame) == pytest.approx(30.0, abs=1.0)

    def test_invalid_region_type_raises(self) -> None:
        with pytest.raises(ValueError):
            VitalsReader(hp_region=(1, 2, 3))  # type: ignore[arg-type]

    def test_profile_region_overridden_by_explicit(self) -> None:
        profile = CalibrationProfile(landmark=(0, 0))
        profile.set_region("hp_bar", Region(0, 0, WIDTH, HEIGHT))
        reader = VitalsReader(profile, hp_region=FULL_REGION)
        # The explicit region should be used.
        assert reader._hp_region == FULL_REGION


class TestPerformance:
    """Per-read latency on a 1080p-derived region crop (budget < 10 ms)."""

    def test_read_under_budget(self) -> None:
        # A 1080p-sized frame; we only process the small bar region, but even
        # timing the full call must stay far under the step budget.
        h, w = 1080, 1920
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:] = (10, 20, 30)
        bar = frame[400:423, 1000:1094]
        bar[:] = BAR_BACKGROUND_RGB
        bar[:, : 47] = HP_FILL_RGB
        region = (1000, 400, 94, 23)

        reader = VitalsReader(hp_region=region)
        # Warm up, then time several reads.
        for _ in range(3):
            reader.read_hp(frame)
        start = time.perf_counter()
        n = 50
        for _ in range(n):
            reader.read_hp(frame)
        elapsed_ms = (time.perf_counter() - start) * 1000.0 / n
        assert elapsed_ms < 10.0, f"read_hp took {elapsed_ms:.3f} ms (budget 10 ms)"
