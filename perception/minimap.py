"""Minimap position reading (T21).

Reads the player position from the Tibia client's minimap/radar widget
(see ``docs/ui_layout.md`` section 4).

Layout facts (measured from PyTibia / tibiaeye):

* Minimap is **106 x 109 px** (:data:`RADAR_WIDTH` / :data:`RADAR_HEIGHT`).
* The player is **always at the center** of the minimap: local coordinates
  ``(53, 54)`` (:data:`PLAYER_CENTER_X` / :data:`PLAYER_CENTER_Y`).
* Radar scale: **1 pixel = 1 world tile**.
* Terrain scrolls around the player: when the player moves, the radar
  content shifts in the *opposite* direction.

How the position is obtained
----------------------------

* **Validation**: the radar is only trusted when the player marker (a small
  green dot, color configurable) is present at the center. If the marker is
  missing the region is not a valid radar and :meth:`read_position` returns
  ``None``. The marker color/tolerance must be confirmed against T08
  screenshots; the default is the classic green dot.
* **(x, y) — absolute**: a pluggable :class:`MapMatcher` converts the radar
  image to world coordinates (map matching against a known map). When
  configured it is authoritative: a ``None`` result yields ``None`` here.
* **(x, y) — relative tracking** (default, no map matcher): the terrain
  displacement between successive radar frames is estimated with phase
  correlation (:func:`cv2.phaseCorrelate`); player displacement is the
  negated terrain displacement, accumulated from ``initial_position``.
  This is the closed-loop tracking technique from ui_layout.md #4.
* **z**: read from an injected :class:`moonlight.perception.floor.FloorReader`
  (or any :class:`FloorSource`). ``0`` means "unknown floor" (see
  ``moonlight.state.Position``).

Frames are RGB ``(H, W, 3)`` ``uint8`` arrays as produced by
``moonlight.capture``. One read (crop + marker check + phase correlation on
a 106x109 radar) is well under the 10 ms budget (T30).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import cv2
import numpy as np

from moonlight.perception.floor import FloorReader
from moonlight.state import Position

__all__ = [
    "Rect",
    "RGB",
    "RADAR_WIDTH",
    "RADAR_HEIGHT",
    "PLAYER_CENTER_X",
    "PLAYER_CENTER_Y",
    "PLAYER_MARKER_RGB",
    "DEFAULT_MARKER_TOLERANCE",
    "DEFAULT_SHIFT_RESPONSE_MIN",
    "MapMatcher",
    "FloorSource",
    "MinimapReader",
]

#: A rectangle as ``(x, y, width, height)`` (matches ``calibrate.Rect``).
Rect = tuple[int, int, int, int]

#: An RGB triple.
RGB = tuple[int, int, int]

#: Canonical minimap width in pixels (ui_layout.md #4).
RADAR_WIDTH = 106

#: Canonical minimap height in pixels (ui_layout.md #4).
RADAR_HEIGHT = 109

#: Player x in local minimap coordinates (center; ui_layout.md #4).
PLAYER_CENTER_X = 53

#: Player y in local minimap coordinates (center; ui_layout.md #4).
PLAYER_CENTER_Y = 54

#: Classic radar player-marker color (green dot). Verify via T08.
PLAYER_MARKER_RGB: RGB = (0, 192, 0)

#: Per-channel tolerance for the player marker (compression artifacts).
DEFAULT_MARKER_TOLERANCE = 60

#: Minimum phase-correlation response for a trusted shift estimate.
DEFAULT_SHIFT_RESPONSE_MIN = 0.6


class MapMatcher(Protocol):
    """Converts a grayscale radar image into absolute world coordinates.

    Implementations perform map matching against a known world map.
    Return ``None`` when the map cannot be located.
    """

    def match(self, radar_gray: np.ndarray) -> tuple[int, int] | None:  # pragma: no cover
        """Return world ``(x, y)`` or ``None`` when unlocatable."""


class FloorSource(Protocol):
    """Anything that can read the current floor level (e.g. ``FloorReader``)."""

    def read_floor(
        self, frame: np.ndarray, region: Rect | None = None
    ) -> int | None:  # pragma: no cover
        """Return the floor index 0..15, or ``None`` on detection failure."""


class MinimapReader:
    """Read the player position (x, y, z) from the minimap region.

    Construct from a :class:`moonlight.calibrate.CalibrationProfile` (which
    stores the ``minimap`` region) and/or an explicit region tuple. The
    stored region is the default when :meth:`read_position` is called
    without an explicit region.

    Example:
        >>> profile = CalibrationProfile.load("profiles/default.json")
        >>> floor = FloorReader(profile)
        >>> reader = MinimapReader(profile, floor_reader=floor)
        >>> frame = capture.get_frame()            # RGB (H, W, 3)
        >>> pos = reader.read_position(frame)      # Position or None
    """

    def __init__(
        self,
        profile: object | None = None,
        *,
        minimap_region: Rect | None = None,
        floor_reader: FloorSource | None = None,
        floor_region: Rect | None = None,
        map_matcher: MapMatcher | None = None,
        initial_position: Position | None = None,
        player_marker_rgb: RGB = PLAYER_MARKER_RGB,
        marker_tolerance: int = DEFAULT_MARKER_TOLERANCE,
        shift_response_min: float = DEFAULT_SHIFT_RESPONSE_MIN,
    ) -> None:
        """Create a reader.

        Args:
            profile: Optional ``CalibrationProfile``. Its ``minimap``
                region is used as the default region.
            minimap_region: Explicit minimap region; overrides the profile.
            floor_reader: Source for the z-level (floor level).
            floor_region: Region for the floor reader; defaults to the
                region stored inside a :class:`FloorReader`.
            map_matcher: Absolute map matcher (see :class:`MapMatcher`).
                When given, it is the sole source of (x, y).
            initial_position: Anchor for relative tracking (used when no
                *map_matcher* is configured): first read reports this.
                Defaults to ``Position(0, 0, 0)`` when ``None``.
            player_marker_rgb: Color of the player marker at the radar
                center.
            marker_tolerance: Per-channel tolerance for the marker color.
            shift_response_min: Minimum phase-correlation response for a
                trusted terrain shift.

        Raises:
            ValueError: If a region is not a 4-element tuple.
        """
        self._minimap_region: Rect | None = None
        if profile is not None:
            regions = getattr(profile, "regions", None)
            if regions:
                minimap = regions.get("minimap")
                if minimap is not None:
                    self._minimap_region = self._as_rect(minimap.to_tuple)

        if minimap_region is not None:
            self._minimap_region = self._as_rect(minimap_region)

        self._floor_reader = floor_reader
        self._floor_region = floor_region
        self._map_matcher = map_matcher
        self._player_marker_rgb = tuple(player_marker_rgb)
        self._marker_tolerance = int(marker_tolerance)
        self._shift_response_min = float(shift_response_min)

        if initial_position is None:
            initial_position = Position(0, 0, 0)
        self._initial_position = initial_position
        self._position = initial_position
        self._prev_gray: np.ndarray | None = None

    # -- public API ---------------------------------------------------------

    @property
    def minimap_region(self) -> Rect | None:
        """The stored minimap region (default for :meth:`read_position`)."""
        return self._minimap_region

    def read_position(
        self,
        frame: np.ndarray,
        region: Rect | None = None,
    ) -> Position | None:
        """Return the player position (x, y, z) from *frame*.

        Args:
            frame: RGB ``(H, W, 3)`` ``uint8`` capture.
            region: Minimap region; defaults to the constructor region.

        Returns:
            The player :class:`~moonlight.state.Position` (z is 0 when the
            floor is unknown), or ``None`` when no region is known, the
            region is out of bounds, or the player marker is not present at
            the radar center (region is not a valid minimap).
        """
        region = region if region is not None else self._minimap_region
        if region is None:
            return None
        crop = self._crop(frame, region)
        if crop is None or crop.size == 0:
            return None
        if not self._marker_at_center(crop):
            return None

        h, w = crop.shape[:2]
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        z = self._read_z(frame)

        if self._map_matcher is not None:
            xy = self._map_matcher.match(gray)
            if xy is None:
                return None
            return Position(int(xy[0]), int(xy[1]), z)

        # Relative tracking: player displacement = negated terrain shift.
        dx = 0
        dy = 0
        if self._prev_gray is not None:
            shift = self._terrain_shift(self._prev_gray, gray)
            if shift is not None:
                # Reject shifts larger than half the radar: phase correlation
                # is ambiguous beyond that (e.g. teleport / radar reset).
                if abs(shift[0]) <= w / 2.0 and abs(shift[1]) <= h / 2.0:
                    dx = -int(round(shift[0]))
                    dy = -int(round(shift[1]))
        self._prev_gray = gray
        self._position = Position(self._position.x + dx, self._position.y + dy, z)
        return self._position

    def reset(self, position: Position | None = None) -> None:
        """Re-anchor relative tracking and drop the previous radar frame.

        Args:
            position: New anchor position; defaults to the constructor
                *initial_position*.
        """
        self._position = position if position is not None else self._initial_position
        self._prev_gray = None

    # -- helpers ------------------------------------------------------------

    def _read_z(self, frame: np.ndarray) -> int:
        """Read the z-level via the floor reader (0 when unknown)."""
        if self._floor_reader is None:
            return 0
        region = self._floor_region
        if region is None and isinstance(self._floor_reader, FloorReader):
            region = self._floor_reader.floor_region
        floor = self._floor_reader.read_floor(frame, region)
        return 0 if floor is None else int(floor)

    def _marker_at_center(self, crop: np.ndarray) -> bool:
        """Check the player marker around the radar center.

        For a canonical 106x109 region the center is exactly (53, 54) in
        local coordinates; for any other region size the crop center is
        used instead.
        """
        h, w = crop.shape[:2]
        if h < 5 or w < 5:
            return False
        cy, cx = h // 2, w // 2
        r = 2
        area = crop[cy - r : cy + r + 1, cx - r : cx + r + 1, :3].astype(np.int16)
        ref = np.array(self._player_marker_rgb, dtype=np.int16)
        matches = (np.abs(area - ref) <= self._marker_tolerance).all(axis=2)
        return bool(matches.any())

    def _terrain_shift(self, prev: np.ndarray, curr: np.ndarray) -> tuple[float, float] | None:
        """Estimate the terrain content shift from *prev* to *curr*.

        Returns ``(dx, dy)`` in pixels (content displacement) or ``None``
        when the phase-correlation response is too low to trust.
        """
        prev_f = prev.astype(np.float32)
        curr_f = curr.astype(np.float32)
        shift, response = cv2.phaseCorrelate(prev_f, curr_f, None)
        if response is None or float(response) < self._shift_response_min:
            return None
        return (float(shift[0]), float(shift[1]))

    @staticmethod
    def _crop(frame: np.ndarray, region: Rect) -> np.ndarray | None:
        """Clip *region* to the frame bounds and return a view of the crop."""
        h, w = frame.shape[:2]
        x, y, rw, rh = region
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(w, int(x) + int(rw))
        y1 = min(h, int(y) + int(rh))
        if x1 <= x0 or y1 <= y0:
            return None
        return frame[y0:y1, x0:x1, :3]

    @staticmethod
    def _as_rect(rect: object) -> Rect:
        """Validate and coerce a region to a ``Rect`` (as in vitals.py)."""
        if not isinstance(rect, Sequence) or isinstance(rect, (str, bytes)):
            raise ValueError(f"Region must be a 4-element sequence, got {rect!r}")
        if len(rect) != 4:
            raise ValueError(f"Region must have 4 values, got {len(rect)}")
        return (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
