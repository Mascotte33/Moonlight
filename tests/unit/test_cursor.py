"""Unit tests for perception/cursor.py (T17 closed-loop clicking — cursor).

Focus:
    - CursorTemplates: validation of template sets (empty, non-2-D, zero-size).
    - CursorReader:
        * read() returns the center of the best template match in frame
          coordinates, at the exact known position of a rendered template;
        * no match / score below threshold -> None (fail-closed);
        * region cropping: reported positions are offset by the region
          origin, including regions clipped at the frame edge;
        * out-of-bounds and negative-size regions -> None;
        * multiple templates (any one may match);
        * template larger than the region -> None;
        * profile-provided cursor region (CalibrationProfile with a
          ``cursor`` region) and explicit-region override;
        * a bare single 2-D array is accepted as one template.

All frames are synthetic RGB (H, W, 3) uint8 arrays; the cursor template is
rendered into the frame at a known center, so the expected match is
deterministic (score ~= 1.0).

Run:
    python -m pytest tests/unit/test_cursor.py
"""

from __future__ import annotations

import numpy as np
import pytest

from moonlight.perception import CursorReader, CursorTemplates
from moonlight.setup.calibrate import CalibrationProfile, Region

# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

FRAME_W, FRAME_H = 320, 200
FULL_REGION = (0, 0, FRAME_W, FRAME_H)
BG = 40  # uniform gray background (no structure -> no false matches)


def _make_cursor_template() -> np.ndarray:
    """A distinctive 11x11 arrow-like pattern (odd size -> clean center)."""
    tpl = np.zeros((11, 11), dtype=np.uint8)
    tpl[:, 5] = 255
    tpl[5, :] = 255
    tpl[1, 1] = 250
    tpl[9, 9] = 200
    tpl[3, 7] = 170
    return tpl


def _make_other_template() -> np.ndarray:
    """A second, different 9x9 pattern for the multiple-template tests."""
    tpl = np.zeros((9, 9), dtype=np.uint8)
    tpl[4, :] = 255
    tpl[:, 4] = 180
    tpl[0, 0] = 240
    tpl[8, 8] = 120
    return tpl


CURSOR_TEMPLATE = _make_cursor_template()
OTHER_TEMPLATE = _make_other_template()


def make_frame(
    center: tuple[int, int] | None = None,
    template: np.ndarray = CURSOR_TEMPLATE,
) -> np.ndarray:
    """Blank RGB frame with *template* rendered centered at *center*.

    The template is drawn as gray RGB (R=G=B), so its grayscale
    conversion equals the template exactly -> match score ~= 1.0.
    """
    frame = np.full((FRAME_H, FRAME_W, 3), BG, dtype=np.uint8)
    if center is not None:
        cx, cy = center
        x0 = cx - template.shape[1] // 2
        y0 = cy - template.shape[0] // 2
        frame[y0 : y0 + template.shape[0], x0 : x0 + template.shape[1]] = (
            template[:, :, np.newaxis]
        )
    return frame


# ---------------------------------------------------------------------------
# CursorTemplates
# ---------------------------------------------------------------------------


class TestCursorTemplates:
    def test_empty_frames_raise(self) -> None:
        with pytest.raises(ValueError):
            CursorTemplates([])

    def test_non_2d_frame_raises(self) -> None:
        with pytest.raises(ValueError):
            CursorTemplates([np.zeros((11, 11, 1), dtype=np.uint8)])

    def test_zero_size_frame_raises(self) -> None:
        with pytest.raises(ValueError):
            CursorTemplates([np.zeros((0, 0), dtype=np.uint8)])

    def test_stores_frames(self) -> None:
        tpl = _make_cursor_template()
        other = _make_other_template()
        set_ = CursorTemplates([tpl, other])
        assert set_.frames == (tpl, other)


class TestCursorReaderConstruction:
    def test_no_region_and_no_templates_accepted(self) -> None:
        # Fail-closed at read time; construction itself is permissive.
        reader = CursorReader()
        assert reader.cursor_region is None
        assert reader.read(make_frame()) is None

    def test_single_bare_array_is_one_template(self) -> None:
        reader = CursorReader(cursor_region=FULL_REGION, cursor_templates=CURSOR_TEMPLATE)
        frame = make_frame(center=(100, 80))
        assert reader.read(frame) == (100, 80)

    def test_empty_template_list_raises(self) -> None:
        with pytest.raises(ValueError):
            CursorReader(cursor_region=FULL_REGION, cursor_templates=[])

    def test_bad_region_shape_raises(self) -> None:
        with pytest.raises(ValueError):
            CursorReader(cursor_region=(0, 0, 10))  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            CursorReader(cursor_region="nope")  # type: ignore[arg-type]

    def test_profile_provided_cursor_region(self) -> None:
        profile = CalibrationProfile(
            landmark=(0, 0),
            regions={"cursor": Region(0, 0, FRAME_W, FRAME_H)},
        )
        reader = CursorReader(profile, cursor_templates=[CURSOR_TEMPLATE])
        assert reader.cursor_region == FULL_REGION
        frame = make_frame(center=(120, 60))
        assert reader.read(frame) == (120, 60)

    def test_profile_without_cursor_region_gives_none(self) -> None:
        profile = CalibrationProfile(landmark=(0, 0))
        reader = CursorReader(profile, cursor_templates=[CURSOR_TEMPLATE])
        assert reader.cursor_region is None
        assert reader.read(make_frame(center=(100, 80))) is None

    def test_explicit_region_overrides_profile(self) -> None:
        profile = CalibrationProfile(
            landmark=(0, 0),
            regions={"cursor": Region(0, 0, FRAME_W, FRAME_H)},
        )
        reader = CursorReader(
            profile,
            cursor_region=(0, 0, 200, 100),
            cursor_templates=[CURSOR_TEMPLATE],
        )
        assert reader.cursor_region == (0, 0, 200, 100)


# ---------------------------------------------------------------------------
# CursorReader.read
# ---------------------------------------------------------------------------


class TestCursorReaderRead:
    def test_returns_center_of_rendered_template(self) -> None:
        reader = CursorReader(cursor_region=FULL_REGION, cursor_templates=[CURSOR_TEMPLATE])
        frame = make_frame(center=(100, 80))
        assert reader.read(frame) == (100, 80)

    def test_center_is_top_left_plus_half_size(self) -> None:
        # Prove the center convention: an odd-sized template rendered with a
        # known top-left must be reported as top-left + size // 2.
        tpl = _make_cursor_template()  # (11, 11)
        top_left = (30, 40)
        expected = (top_left[0] + tpl.shape[1] // 2, top_left[1] + tpl.shape[0] // 2)
        frame = np.full((FRAME_H, FRAME_W, 3), BG, dtype=np.uint8)
        frame[top_left[1] : top_left[1] + 11, top_left[0] : top_left[0] + 11] = tpl[
            :, :, np.newaxis
        ]
        reader = CursorReader(cursor_region=FULL_REGION, cursor_templates=[tpl])
        assert reader.read(frame) == expected

    def test_none_when_cursor_absent(self) -> None:
        reader = CursorReader(cursor_region=FULL_REGION, cursor_templates=[CURSOR_TEMPLATE])
        assert reader.read(make_frame(center=None)) is None

    def test_none_when_score_below_threshold(self) -> None:
        # A threshold above 1.0 can never be reached.
        reader = CursorReader(
            cursor_region=FULL_REGION,
            cursor_templates=[CURSOR_TEMPLATE],
            match_threshold=1.01,
        )
        frame = make_frame(center=(100, 80))
        assert reader.read(frame) is None

    def test_found_at_low_threshold(self) -> None:
        reader = CursorReader(
            cursor_region=FULL_REGION,
            cursor_templates=[CURSOR_TEMPLATE],
            match_threshold=0.5,
        )
        frame = make_frame(center=(100, 80))
        assert reader.read(frame) == (100, 80)

    def test_region_offset_applied_to_reported_position(self) -> None:
        # Region origin (20, 30): a cursor at region-local (50, 40) is at
        # frame (70, 70) and must be reported in frame coordinates.
        reader = CursorReader(
            cursor_region=(20, 30, 200, 140), cursor_templates=[CURSOR_TEMPLATE]
        )
        frame = make_frame(center=(70, 70))
        assert reader.read(frame) == (70, 70)

    def test_region_clipped_at_left_edge(self) -> None:
        # Region extends 20 px past the left frame edge: reported positions
        # must still be in frame coordinates (offset by the clipped origin).
        reader = CursorReader(
            cursor_region=(-20, 0, 240, FRAME_H), cursor_templates=[CURSOR_TEMPLATE]
        )
        frame = make_frame(center=(10, 80))
        assert reader.read(frame) == (10, 80)

    def test_region_clipped_at_top_edge(self) -> None:
        reader = CursorReader(
            cursor_region=(0, -30, FRAME_W, FRAME_H + 30),
            cursor_templates=[CURSOR_TEMPLATE],
        )
        frame = make_frame(center=(10, 80))
        assert reader.read(frame) == (10, 80)

    def test_region_outside_frame_returns_none(self) -> None:
        reader = CursorReader(
            cursor_region=(400, 300, 50, 50), cursor_templates=[CURSOR_TEMPLATE]
        )
        frame = make_frame(center=(100, 80))
        assert reader.read(frame) is None

    def test_negative_size_region_returns_none(self) -> None:
        reader = CursorReader(
            cursor_region=(0, 0, -10, 20), cursor_templates=[CURSOR_TEMPLATE]
        )
        frame = make_frame(center=(100, 80))
        assert reader.read(frame) is None

    def test_multiple_templates_any_may_match(self) -> None:
        reader = CursorReader(
            cursor_region=FULL_REGION,
            cursor_templates=[CURSOR_TEMPLATE, OTHER_TEMPLATE],
        )
        # Only the second template is present in the frame.
        frame = make_frame(center=(140, 120), template=OTHER_TEMPLATE)
        assert reader.read(frame) == (140, 120)

    def test_template_larger_than_region_returns_none(self) -> None:
        big = np.full((40, 40), 200, dtype=np.uint8)
        big[20, :] = 255
        big[:, 20] = 100
        reader = CursorReader(
            cursor_region=(0, 0, 20, 20), cursor_templates=[big]
        )
        frame = make_frame(center=(100, 80))
        assert reader.read(frame) is None

    def test_read_region_param_overrides_constructor(self) -> None:
        reader = CursorReader(
            cursor_region=(0, 0, FRAME_W, FRAME_H), cursor_templates=[CURSOR_TEMPLATE]
        )
        frame = make_frame(center=(100, 80))
        # A region that does not cover the cursor -> None.
        assert reader.read(frame, region=(200, 0, 100, FRAME_H)) is None
        # A shifted region that does cover it -> same frame coordinates.
        assert reader.read(frame, region=(0, 0, FRAME_W, FRAME_H)) == (100, 80)

    def test_returns_none_without_templates(self) -> None:
        reader = CursorReader(cursor_region=FULL_REGION)
        frame = make_frame(center=(100, 80))
        assert reader.read(frame) is None


def test_os_cursor_reader_maps_the_screen_cursor_into_the_game_area() -> None:
    from moonlight.perception.cursor import OsCursorReader

    frame = np.zeros((10, 10, 3), np.uint8)
    reader = OsCursorReader(lambda: (100, 50), screen_cursor=lambda: (130, 70))
    assert reader.read(frame) == (30, 20)
    assert reader.cursor_region is not None
    assert OsCursorReader(lambda: (0, 0), screen_cursor=lambda: None).read(frame) is None


def test_clicker_converges_with_the_os_cursor_reader() -> None:
    from moonlight.hardware.clicker import ClickController
    from moonlight.hardware.input import FakeInput
    from moonlight.perception.cursor import OsCursorReader

    fake = FakeInput(cursor=(900, 700))  # screen coordinates
    reader = OsCursorReader(lambda: (500, 300), screen_cursor=lambda: fake.cursor_position)
    frame = np.zeros((10, 10, 3), np.uint8)
    clicker = ClickController(fake, reader, lambda: frame, sleep_fn=lambda _s: None)
    assert clicker.click_at((40, 60))
    assert fake.cursor_position == (540, 360)
