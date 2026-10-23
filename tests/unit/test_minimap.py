"""Unit tests for moonlight.perception.minimap.
All tests are offline and use SYNTHETIC numpy radar frames -- no live
client and no golden screenshots are required. The golden-image tests
live in ``tests/perf/test_minimap_golden.py`` and skip until real frames
are provided (T08).

The synthetic radars mimic the measured layout from docs/ui_layout.md #4:
a 106x109 widget with the player marker at local (53, 54) and terrain
that shifts between frames (terrain motion = -player motion). Terrain is
deterministic per-seed uniform noise so phase correlation has a strong,
unambiguous response.
"""
from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from moonlight.bot.state import Position
from moonlight.perception.minimap import (
    DEFAULT_MARKER_TOLERANCE,
    PLAYER_CENTER_X,
    PLAYER_CENTER_Y,
    PLAYER_MARKER_RGB,
    RADAR_HEIGHT,
    RADAR_WIDTH,
    MinimapReader,
)
from moonlight.setup.calibrate import CalibrationProfile, Region

REGION_X, REGION_Y = 100, 80

MARKER_RGB = PLAYER_MARKER_RGB  # (255, 255, 255)


def make_terrain(seed: int, shift: tuple[int, int] = (0, 0)) -> np.ndarray:
    """A 106x109 grayscale terrain image (uniform noise, shifted).

    Capped below the white-cross marker's tolerance band so random terrain
    never passes for the player marker.
    """
    rng = np.random.RandomState(seed)
    terrain = rng.uniform(0, 180, (RADAR_HEIGHT, RADAR_WIDTH))
    terrain = terrain.astype(np.uint8)
    if shift:
        terrain = np.roll(np.roll(terrain, shift[0], axis=1), shift[1], axis=0)
    return terrain


def make_frame(
    seed: int,
    offset: tuple[int, int] = (REGION_X, REGION_Y),
    marker: bool = True,
    marker_rgb: tuple[int, int, int] = MARKER_RGB,
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Build a synthetic frame with a radar at *offset*.

    *marker* controls whether the player marker is drawn at the radar
    center (local 53, 54).

    Returns ``(frame, region)`` where *region* covers exactly the radar.
    """
    ox, oy = offset
    height = oy + RADAR_HEIGHT + 10
    width = ox + RADAR_WIDTH + 10
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (5, 5, 5)
    terrain = make_terrain(seed)
    region = (ox, oy, RADAR_WIDTH, RADAR_HEIGHT)
    frame[oy : oy + RADAR_HEIGHT, ox : ox + RADAR_WIDTH] = terrain[:, :, None]
    if marker:
        cx, cy = ox + PLAYER_CENTER_X, oy + PLAYER_CENTER_Y
        frame[cy, cx] = marker_rgb
    return frame, region


def make_reader(**kwargs: object) -> MinimapReader:
    """A MinimapReader with the standard test region."""
    return MinimapReader(minimap_region=(REGION_X, REGION_Y, RADAR_WIDTH, RADAR_HEIGHT), **kwargs)  # type: ignore[arg-type]


class TestConstants:
    """Layout constants from docs/ui_layout.md #4."""

    def test_radar_dimensions(self) -> None:
        assert RADAR_WIDTH == 106
        assert RADAR_HEIGHT == 109

    def test_player_center(self) -> None:
        assert PLAYER_CENTER_X == 53
        assert PLAYER_CENTER_Y == 54
        # The center is exactly mid-widget: (106 // 2, 109 // 2).
        assert PLAYER_CENTER_X == RADAR_WIDTH // 2
        assert PLAYER_CENTER_Y == RADAR_HEIGHT // 2

    def test_default_marker(self) -> None:
        assert len(MARKER_RGB) == 3
        assert DEFAULT_MARKER_TOLERANCE > 0


class TestMarkerValidation:
    """The radar is only trusted when the player marker is present."""

    def test_marker_present_returns_position(self) -> None:
        frame, region = make_frame(1, marker=True)
        pos = make_reader().read_position(frame, region)
        assert pos is not None
        assert pos == Position(0, 0, 0)

    def test_marker_absent_returns_none(self) -> None:
        frame, region = make_frame(1, marker=False)
        assert make_reader().read_position(frame, region) is None

    def test_old_green_dot_is_not_the_marker(self) -> None:
        # Regression (T28): this client's marker is a white cross; the old
        # green-dot default matched none of the 200 golden frames.
        frame, region = make_frame(1, marker=True, marker_rgb=(0, 192, 0))
        assert make_reader().read_position(frame, region) is None

    def test_wrong_marker_color_returns_none(self) -> None:
        # A red dot at the center is not the player marker.
        frame, region = make_frame(1, marker=True, marker_rgb=(200, 20, 20))
        assert make_reader().read_position(frame, region) is None

    def test_marker_within_tolerance(self) -> None:
        # White cross off by less than the per-channel tolerance: accepted.
        frame, region = make_frame(1, marker=True, marker_rgb=(250, 245, 242))
        pos = make_reader(marker_tolerance=15).read_position(frame, region)
        assert pos is not None

    def test_marker_outside_tolerance(self) -> None:
        # Light grey off by more than a strict tolerance: rejected.
        frame, region = make_frame(1, marker=True, marker_rgb=(230, 230, 230))
        assert make_reader(marker_tolerance=10).read_position(frame, region) is None


class TestRegionHandling:
    """Region clipping and placement (window can move/resize)."""

    def test_region_at_offset(self) -> None:
        offset = (300, 420)
        frame, region = make_frame(1, offset=offset)
        reader = MinimapReader(minimap_region=region)
        pos = reader.read_position(frame)
        assert pos is not None

    def test_region_outside_frame(self) -> None:
        frame, _ = make_frame(1)
        h, w = frame.shape[:2]
        reader = make_reader()
        assert reader.read_position(frame, (w + 5, h + 5, RADAR_WIDTH, RADAR_HEIGHT)) is None

    def test_no_region_returns_none(self) -> None:
        frame, _ = make_frame(1)
        assert MinimapReader().read_position(frame) is None

    def test_default_region_from_profile(self) -> None:
        frame, region = make_frame(1)
        profile = CalibrationProfile(landmark=(0, 0))
        profile.set_region("minimap", Region(*region))
        pos = MinimapReader(profile).read_position(frame)
        assert pos is not None

    def test_explicit_region_overrides_profile(self) -> None:
        profile = CalibrationProfile(landmark=(0, 0))
        profile.set_region("minimap", Region(0, 0, RADAR_WIDTH, RADAR_HEIGHT))
        reader = MinimapReader(profile, minimap_region=(9, 9, RADAR_WIDTH, RADAR_HEIGHT))
        assert reader.minimap_region == (9, 9, RADAR_WIDTH, RADAR_HEIGHT)

    def test_invalid_region_raises(self) -> None:
        with pytest.raises(ValueError):
            MinimapReader(minimap_region=(1, 2, 3))  # type: ignore[arg-type]


class TestRelativeTracking:
    """Position from terrain displacement (closed-loop tracking)."""

    def test_first_read_is_initial_position(self) -> None:
        frame, region = make_frame(1)
        reader = make_reader(initial_position=Position(100, 200, 0))
        pos = reader.read_position(frame, region)
        assert pos == Position(100, 200, 0)

    def test_minimap_absolute_seed(self) -> None:
        """F-03: an absolute start seed makes reported (x, y) positions absolute.

        A MinimapReader constructed with a known seed reports that seed as
        the starting absolute position (z comes from the floor reader, 0 when
        unknown), and a known relative marker offset maps to the correct
        absolute coordinate (seed + displacement). This is the perception
        side of F-03: one absolute-tracked position that perception owns,
        seeded from the last known position.
        """
        seed = Position(100, 200, 0)
        reader = make_reader(initial_position=seed)
        frame1, region = make_frame(1)
        # The first read reports the seed as the absolute starting position.
        assert reader.read_position(frame1, region) == seed
        # Move right by 3: the absolute position is seed + (3, 0, 0).
        frame2, _ = make_frame(1)
        terrain = make_terrain(1)
        frame2[REGION_Y : REGION_Y + RADAR_HEIGHT, REGION_X : REGION_X + RADAR_WIDTH] = (
            np.roll(terrain, -3, axis=1)[:, :, None]
        )
        frame2[REGION_Y + PLAYER_CENTER_Y, REGION_X + PLAYER_CENTER_X] = MARKER_RGB
        assert reader.read_position(frame2, region) == Position(103, 200, 0)

    def test_move_right(self) -> None:
        # Player moves right by 3: terrain shifts left by 3 (dx = -3).
        reader = make_reader(initial_position=Position(10, 10, 0))
        frame1, region = make_frame(1)
        assert reader.read_position(frame1, region) == Position(10, 10, 0)
        frame2, _ = make_frame(1)
        terrain = make_terrain(1)
        frame2[REGION_Y : REGION_Y + RADAR_HEIGHT, REGION_X : REGION_X + RADAR_WIDTH] = (
            np.roll(terrain, -3, axis=1)[:, :, None]
        )
        frame2[REGION_Y + PLAYER_CENTER_Y, REGION_X + PLAYER_CENTER_X] = MARKER_RGB
        pos = reader.read_position(frame2, region)
        assert pos == Position(13, 10, 0)

    def test_move_up(self) -> None:
        # Player moves up by 2: terrain shifts down by 2 (dy = +2).
        reader = make_reader(initial_position=Position(10, 10, 0))
        frame1, region = make_frame(1)
        reader.read_position(frame1, region)
        frame2, _ = make_frame(1)
        terrain = make_terrain(1)
        frame2[REGION_Y : REGION_Y + RADAR_HEIGHT, REGION_X : REGION_X + RADAR_WIDTH] = (
            np.roll(terrain, 2, axis=0)[:, :, None]
        )
        frame2[REGION_Y + PLAYER_CENTER_Y, REGION_X + PLAYER_CENTER_X] = MARKER_RGB
        pos = reader.read_position(frame2, region)
        assert pos == Position(10, 8, 0)

    def test_position_accumulates_across_frames(self) -> None:
        reader = make_reader(initial_position=Position(0, 0, 0))
        region = (REGION_X, REGION_Y, RADAR_WIDTH, RADAR_HEIGHT)
        terrain = make_terrain(2)
        # First frame anchors the tracking (no shift on the first read).
        frame0, _ = make_frame(2)
        assert reader.read_position(frame0, region) == Position(0, 0, 0)
        for step in range(1, 4):  # three 1-tile steps to the right
            frame, _ = make_frame(2)
            shifted = np.roll(terrain, -step, axis=1)
            frame[REGION_Y : REGION_Y + RADAR_HEIGHT, REGION_X : REGION_X + RADAR_WIDTH] = (
                shifted[:, :, None]
            )
            frame[REGION_Y + PLAYER_CENTER_Y, REGION_X + PLAYER_CENTER_X] = MARKER_RGB
            pos = reader.read_position(frame, region)
            assert pos is not None
            assert pos == Position(step, 0, 0), f"step {step}: {pos}"

    def test_no_shift_keeps_position(self) -> None:
        reader = make_reader(initial_position=Position(5, 6, 0))
        frame1, region = make_frame(3)
        reader.read_position(frame1, region)
        frame2, _ = make_frame(3)  # identical terrain
        pos = reader.read_position(frame2, region)
        assert pos == Position(5, 6, 0)

    def test_ambiguous_shift_rejected_by_half_radar_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F-17: a shift beyond half the radar flags lost (no silent 0 shift)."""
        import cv2

        reader = make_reader(initial_position=Position(7, 7, 0))
        region = (REGION_X, REGION_Y, RADAR_WIDTH, RADAR_HEIGHT)
        frame1, _ = make_frame(4)
        assert reader.read_position(frame1, region) == Position(7, 7, 0)
        # Force phaseCorrelate to report a huge (aliased/ambiguous) shift.
        monkeypatch.setattr(cv2, "phaseCorrelate", lambda a, b, w: ((70.0, 0.0), 1.0))
        frame2, _ = make_frame(4)
        assert reader.read_position(frame2, region) is None
        assert reader.lost
        monkeypatch.undo()
        # Stays lost (None) until re-anchored.
        assert reader.read_position(frame2, region) is None
        reader.reset(Position(40, 41, 0))
        assert not reader.lost
        assert reader.read_position(frame2, region) == Position(40, 41, 0)

    def test_reset_reanchors_tracking(self) -> None:
        reader = make_reader(initial_position=Position(0, 0, 0))
        frame, region = make_frame(5)
        reader.read_position(frame, region)
        reader.reset(Position(50, 60, 0))
        pos = reader.read_position(frame, region)
        assert pos == Position(50, 60, 0)

    def test_reset_without_args_uses_initial(self) -> None:
        reader = make_reader(initial_position=Position(4, 4, 0))
        frame, region = make_frame(5)
        reader.read_position(frame, region)
        reader.reset()
        pos = reader.read_position(frame, region)
        assert pos == Position(4, 4, 0)


def shifted_frame(seed: int, dx: int, marker: bool = True) -> np.ndarray:
    """Frame whose radar terrain is *seed* rolled by *dx* (player moved -dx)."""
    frame, _ = make_frame(seed, marker=False)
    terrain = np.roll(make_terrain(seed), dx, axis=1)
    frame[REGION_Y : REGION_Y + RADAR_HEIGHT, REGION_X : REGION_X + RADAR_WIDTH] = terrain[
        :, :, None
    ]
    if marker:
        frame[REGION_Y + PLAYER_CENTER_Y, REGION_X + PLAYER_CENTER_X] = MARKER_RGB
    return frame


class TestShiftValidation:
    """F-17: trust the shift response, track through occlusion, flag jumps."""

    REGION = (REGION_X, REGION_Y, RADAR_WIDTH, RADAR_HEIGHT)

    def test_tracks_through_occluded_marker(self) -> None:
        # A map mark hides the cross; the trusted terrain shift still counts.
        reader = make_reader(initial_position=Position(10, 10, 0))
        assert reader.read_position(shifted_frame(6, 0), self.REGION) == Position(10, 10, 0)
        for step in (1, 2, 3):
            pos = reader.read_position(shifted_frame(6, -step, marker=False), self.REGION)
            assert pos == Position(10 + step, 10, 0)
        assert reader.read_position(shifted_frame(6, -5), self.REGION) == Position(15, 10, 0)
        assert not reader.lost

    def test_no_marker_and_no_trusted_shift_returns_none_and_keeps_anchor(self) -> None:
        reader = make_reader(initial_position=Position(10, 10, 0))
        reader.read_position(shifted_frame(6, 0), self.REGION)
        # Unrelated content (e.g. another panel) without the cross: rejected...
        assert reader.read_position(shifted_frame(99, 0, marker=False), self.REGION) is None
        assert not reader.lost
        # ...and the next radar still correlates against the last trusted one.
        assert reader.read_position(shifted_frame(6, -2), self.REGION) == Position(12, 10, 0)

    def test_radar_change_on_same_floor_flags_lost(self) -> None:
        reader = make_reader(initial_position=Position(10, 10, 0))
        reader.read_position(shifted_frame(6, 0), self.REGION)
        # Cross visible, terrain unrelated, floor unchanged: a teleport.
        assert reader.read_position(shifted_frame(7, 0), self.REGION) is None
        assert reader.lost

    def test_radar_change_with_floor_change_keeps_xy(self) -> None:
        floors = iter([7, 6])

        class _Floor:
            def read_floor(
                self, frame: np.ndarray, region: tuple[int, int, int, int] | None = None
            ) -> int | None:
                return next(floors)

        reader = make_reader(initial_position=Position(10, 10, 7), floor_reader=_Floor())
        assert reader.read_position(shifted_frame(6, 0), self.REGION) == Position(10, 10, 7)
        # Stairs: the radar redraws for the new floor.
        assert reader.read_position(shifted_frame(7, 0), self.REGION) == Position(10, 10, 6)
        assert not reader.lost


class TestMapMatcher:
    """Absolute positioning via a pluggable MapMatcher."""

    def test_matcher_provides_position(self) -> None:
        class _Matcher:
            def match(
                self, radar_rgb: np.ndarray, z: int, near: tuple[int, int] | None = None
            ) -> tuple[int, int] | None:
                return (111, 222)

        reader = make_reader(map_matcher=_Matcher())
        frame, region = make_frame(6)
        pos = reader.read_position(frame, region)
        assert pos == Position(111, 222, 0)

    def test_matcher_none_returns_none(self) -> None:
        class _Matcher:
            def match(
                self, radar_rgb: np.ndarray, z: int, near: tuple[int, int] | None = None
            ) -> tuple[int, int] | None:
                return None

        reader = make_reader(map_matcher=_Matcher())
        frame, region = make_frame(6)
        assert reader.read_position(frame, region) is None

    def test_matcher_used_even_on_first_read(self) -> None:
        # Absolute matching does not depend on the tracking anchor.
        class _Matcher:
            def match(
                self, radar_rgb: np.ndarray, z: int, near: tuple[int, int] | None = None
            ) -> tuple[int, int] | None:
                return (1, 2)

        reader = make_reader(
            map_matcher=_Matcher(), initial_position=Position(100, 100, 0)
        )
        frame, region = make_frame(6)
        pos = reader.read_position(frame, region)
        assert pos == Position(1, 2, 0)

    def test_no_match_estimates_then_goes_lost(self) -> None:
        answers: list[tuple[int, int] | None] = [(100, 100)]
        now = [0.0]

        class _Matcher:
            def match(
                self, radar_rgb: np.ndarray, z: int, near: tuple[int, int] | None = None
            ) -> tuple[int, int] | None:
                return answers.pop() if answers else None

        reader = make_reader(
            map_matcher=_Matcher(), px_per_sqm=1.0, clock=lambda: now[0]
        )
        frame, region = make_frame(1)
        assert reader.read_position(frame, region) == Position(100, 100, 0)
        # No match: the terrain shift moves the estimate 3 tiles right.
        frame2, _ = make_frame(1)
        frame2[REGION_Y : REGION_Y + RADAR_HEIGHT, REGION_X : REGION_X + RADAR_WIDTH] = (
            np.roll(make_terrain(1), -3, axis=1)[:, :, None]
        )
        assert reader.read_position(frame2, region) == Position(103, 100, 0)
        assert reader.estimated and not reader.lost
        now[0] = 11.0
        assert reader.read_position(frame2, region) is None
        assert reader.lost


class TestFloorIntegration:
    """z comes from an injected FloorSource (0 when unknown)."""

    def test_z_from_floor_source(self) -> None:
        class _Floor:
            def read_floor(
                self,
                frame: np.ndarray,
                region: tuple[int, int, int, int] | None = None,
            ) -> int | None:
                return 3

        reader = make_reader(floor_reader=_Floor())
        frame, region = make_frame(7)
        pos = reader.read_position(frame, region)
        assert pos is not None
        assert pos.z == 3

    def test_z_none_when_floor_unknown(self) -> None:
        class _Floor:
            def read_floor(
                self,
                frame: np.ndarray,
                region: tuple[int, int, int, int] | None = None,
            ) -> int | None:
                return None

        reader = make_reader(floor_reader=_Floor())
        frame, region = make_frame(7)
        pos = reader.read_position(frame, region)
        assert pos is not None
        assert pos.z == 0

    def test_z_zero_without_floor_source(self) -> None:
        frame, region = make_frame(7)
        pos = make_reader().read_position(frame, region)
        assert pos is not None
        assert pos.z == 0

    def test_explicit_floor_region_passed(self) -> None:
        calls: list[tuple[int, int, int, int] | None] = []

        class _Floor:
            def read_floor(
                self,
                frame: np.ndarray,
                region: tuple[int, int, int, int] | None = None,
            ) -> int | None:
                calls.append(region)
                return 5

        region = (9, 12, 14, 10)
        reader = make_reader(floor_reader=_Floor(), floor_region=region)
        frame, radar = make_frame(7)
        reader.read_position(frame, radar)
        assert calls == [region]


class TestPerformance:
    """Per-read latency on a 1080p frame (budget < 10 ms, T30)."""

    def test_read_under_10_ms(self) -> None:
        h, w = 1080, 1920
        ox, oy = 400, 300
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:] = (5, 5, 5)
        terrain = make_terrain(9)
        frame[oy : oy + RADAR_HEIGHT, ox : ox + RADAR_WIDTH] = terrain[:, :, None]
        frame[oy + PLAYER_CENTER_Y, ox + PLAYER_CENTER_X] = MARKER_RGB
        region = (ox, oy, RADAR_WIDTH, RADAR_HEIGHT)

        reader = MinimapReader(minimap_region=region)
        # Warm up, then time several reads.
        for _ in range(3):
            reader.read_position(frame, region)
        start = time.perf_counter()
        iterations = 50
        for _ in range(iterations):
            reader.read_position(frame, region)
        elapsed_ms = (time.perf_counter() - start) * 1000.0 / iterations
        assert elapsed_ms < 10.0, f"read_position took {elapsed_ms:.3f} ms (budget 10 ms)"
        assert reader.read_position(frame, region) is not None


def test_ui_scale_reads_an_enlarged_minimap() -> None:
    # Nearest keeps the 1 px marker white; smooth enlarging greys it out of tolerance.
    def frames(scale: float) -> list[np.ndarray]:
        out = []
        for shift in [(0, 0), (-3, 0), (-3, 2)]:  # start, 3 right, then 2 up
            frame, _ = make_frame(1)
            radar = frame[REGION_Y : REGION_Y + RADAR_HEIGHT, REGION_X : REGION_X + RADAR_WIDTH]
            radar[:] = make_terrain(1, shift)[:, :, None]
            radar[PLAYER_CENTER_Y, PLAYER_CENTER_X] = MARKER_RGB
            out.append(cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST))
        return out

    def track(scale: float) -> list[Position | None]:
        region = tuple(round(v * scale) for v in (REGION_X, REGION_Y, RADAR_WIDTH, RADAR_HEIGHT))
        reader = MinimapReader(
            CalibrationProfile(ui_scale=scale),
            minimap_region=region,  # type: ignore[arg-type]
            initial_position=Position(10, 10, 0),
        )
        return [reader.read_position(f) for f in frames(scale)]

    assert track(1.0) == [Position(10, 10, 0), Position(13, 10, 0), Position(13, 8, 0)]
    assert track(1.5) == track(1.0)
