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

* **Validation** (F-17): a read is trusted when the player marker (a small
  white cross, color configurable) is at the center **or** the terrain
  shift against the last trusted radar has a phase-correlation response of
  at least ``shift_response_min``. Map-mark icons over the center hid the
  cross on 27/200 golden frames; tracking continues through that
  occlusion. The very first read (nothing to correlate against) still
  needs the cross. Neither cue -> ``None`` and the last trusted radar is
  kept.
* **Lost** (F-17): a trusted shift beyond half the radar (aliased:
  teleport, very fast move), or an untrusted one with the cross visible
  and the floor unchanged, means the offset is unknown. The reader then
  sets :attr:`MinimapReader.lost` and returns ``None`` until
  :meth:`MinimapReader.reset` re-anchors it -- never a silent zero shift.
  An untrusted shift *with* a floor change is the radar redrawing for
  stairs/ropes: x, y are kept (the step is at most a tile).
* **(x, y) — absolute**: a pluggable :class:`MapMatcher` (the bot uses
  :class:`moonlight.perception.worldmap.WorldMap`) finds the radar on the
  map, near the last position first. With no match the position is moved
  by the terrain shift (kept when the shift is not trusted either) and is
  :attr:`MinimapReader.estimated`; after ``estimate_for`` seconds without a
  match the reader is :attr:`lost` until the next match.
* **(x, y) — relative tracking** (default, no map matcher): the terrain
  displacement between successive radar frames is estimated with phase
  correlation (:func:`cv2.phaseCorrelate`); player displacement is the
  negated terrain displacement, accumulated from ``initial_position``.
  This is the closed-loop tracking technique from ui_layout.md #4.
* **z**: read from an injected :class:`moonlight.perception.floor.FloorReader`
  (or any :class:`FloorSource`). ``0`` means "unknown floor" (see
  ``moonlight.core.state.Position``).

Frames are RGB ``(H, W, 3)`` ``uint8`` arrays as produced by
``moonlight.perception.capture``. One read (crop + marker check + phase correlation on
a 106x109 radar) is well under the 10 ms budget (T30).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Protocol

import cv2
import numpy as np

from moonlight.core.state import Position
from moonlight.perception.floor import FloorReader

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

logger = logging.getLogger(__name__)

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

#: Player-marker color: this client draws a white cross at the radar center
#: (measured on the T08 golden frames; the old green-dot default never
#: matched). Snow-white terrain under the center can false-positive.
PLAYER_MARKER_RGB: RGB = (255, 255, 255)

#: Per-channel tolerance for the player marker (compression artifacts).
DEFAULT_MARKER_TOLERANCE = 60

#: Minimum phase-correlation response for a trusted shift estimate.
DEFAULT_SHIFT_RESPONSE_MIN = 0.6


class MapMatcher(Protocol):
    """Finds an RGB radar crop on the world map of floor *z*.

    With *near*, only the area around that ``(x, y)`` is searched.
    Return ``None`` when the map cannot be located.
    """

    def match(
        self, radar_rgb: np.ndarray, z: int, near: tuple[int, int] | None = None
    ) -> tuple[int, int] | None:  # pragma: no cover
        """Return world ``(x, y)`` of the radar center or ``None``."""


class FloorSource(Protocol):
    """Anything that can read the current floor level (e.g. ``FloorReader``)."""

    def read_floor(
        self, frame: np.ndarray, region: Rect | None = None
    ) -> int | None:  # pragma: no cover
        """Return the floor index 0..15, or ``None`` on detection failure."""


class MinimapReader:
    """Read the player position (x, y, z) from the minimap region.

    Construct from a :class:`moonlight.calibration.calibrate.CalibrationProfile` (which
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
        px_per_sqm: float | None = None,
        estimate_for: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
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
                When given, matches are the source of (x, y) and the
                terrain shift only bridges reads without a match.
            initial_position: Anchor for relative tracking; with a
                *map_matcher*, the last known position to search near.
                Defaults to ``Position(0, 0, 0)`` (unknown with a matcher).
            player_marker_rgb: Color of the player marker at the radar
                center.
            marker_tolerance: Per-channel tolerance for the marker color.
            shift_response_min: Minimum phase-correlation response for a
                trusted terrain shift.
            px_per_sqm: Radar pixels per tile; defaults to the ui_scale.
            estimate_for: Seconds a *map_matcher* reader keeps estimating
                from the terrain shift before it is :attr:`lost`.
            clock: Monotonic clock for *estimate_for*.

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

        # doc: docs/prs/gui-calibration.md#ui-scale
        # One world tile is ui_scale px; the radar size and center follow the crop.
        self._scale = float(getattr(profile, "ui_scale", 1.0))
        self._px = float(px_per_sqm) if px_per_sqm is not None else self._scale
        self._estimate_for = float(estimate_for)
        self._clock = clock
        self._estimated_since: float | None = None
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
        self._prev_z = initial_position.z
        self._lost = map_matcher is not None and initial_position == Position(0, 0, 0)

    # -- public API ---------------------------------------------------------

    @property
    def minimap_region(self) -> Rect | None:
        """The stored minimap region (default for :meth:`read_position`)."""
        return self._minimap_region

    @property
    def lost(self) -> bool:
        """True after an untrackable jump, until :meth:`reset` or a map match (F-17)."""
        return self._lost

    @property
    def estimated(self) -> bool:
        """True while the position comes from the terrain shift, not a map match."""
        return self._estimated_since is not None and not self._lost

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
            The player :class:`~moonlight.core.state.Position` (z is 0 when the
            floor is unknown), or ``None`` when no region is known, the
            region is out of bounds, the radar can't be validated (no
            marker and no trusted shift), or tracking is :attr:`lost`.
        """
        region = region if region is not None else self._minimap_region
        if region is None or (self._lost and self._map_matcher is None):
            return None
        crop = self._crop(frame, region)
        if crop is None or crop.size == 0:
            return None
        h, w = crop.shape[:2]
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)

        if self._map_matcher is not None:
            z = self._read_z(frame)
            if self._radar_unchanged_since_last_match(gray, z):
                return self._position
            near = None if self._lost else (self._position.x, self._position.y)
            xy = self._map_matcher.match(crop, z, near)
            if xy is None and near is not None:
                xy = self._map_matcher.match(crop, z, None)
            if xy is not None:
                self._position = Position(int(xy[0]), int(xy[1]), z)
                self._px = float(getattr(self._map_matcher, "zoom", self._px))
                self._lost = False
                self._estimated_since = None
                self._prev_gray, self._prev_z = gray, z
                return self._position
            if self._lost:
                return None
            now = self._clock()
            if self._estimated_since is None:
                self._estimated_since = now
            elif now - self._estimated_since > self._estimate_for:
                self._lose(f"no map match for {self._estimate_for:.0f} s")
                return None
            marker = True  # the map match, not the cross, validates this radar
        else:
            marker = self._marker_at_center(crop)

        # Relative tracking: player displacement = negated terrain shift.
        shift = None if self._prev_gray is None else self._terrain_shift(self._prev_gray, gray)
        if shift is None and not marker:
            return None  # neither cue: not a radar we can trust; keep the last one
        z = self._read_z(frame)
        dx = dy = 0
        if shift is not None:
            if abs(shift[0]) > w / 2.0 or abs(shift[1]) > h / 2.0:
                # Phase correlation aliases beyond half the radar.
                self._lose(f"shift {shift} beyond half the radar")
                return None
            dx = -int(round(shift[0] / self._px))
            dy = -int(round(shift[1] / self._px))
        elif self._prev_gray is not None and z == self._prev_z:
            if self._map_matcher is not None:
                # doc: docs/prs/position-lost.md#keep-estimating
                # No match and no trusted shift this frame (a map mark, a
                # tooltip): keep the last position as the estimate; the
                # match decides, and estimate_for still ends a real loss.
                return self._position
            # Radar changed with no floor change: teleport or a big jump.
            self._lose("radar changed with no trusted shift")
            return None
        self._prev_gray = gray
        self._prev_z = z
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
        self._prev_z = self._position.z
        self._lost = False
        self._estimated_since = None

    # -- helpers ------------------------------------------------------------

    # doc: docs/prs/tick-speed.md#skip-unchanged-radar
    def _radar_unchanged_since_last_match(self, gray: np.ndarray, z: int) -> bool:
        """True when the radar is pixel-identical to the last map-matched one.

        The world-map match is most of a tick's screen reading; standing
        still or between steps the radar does not change, so the matched
        position still holds.
        """
        return (
            not self._lost
            and self._estimated_since is None
            and self._prev_gray is not None
            and z == self._prev_z
            and np.array_equal(gray, self._prev_gray)
        )

    def _lose(self, why: str) -> None:
        """Flag tracking as lost (F-17): positions are unknown until reset."""
        logger.warning("minimap: tracking lost (%s); re-anchor with reset()", why)
        self._lost = True
        self._prev_gray = None

    def _read_z(self, frame: np.ndarray) -> int:
        """Read the z-level via the floor reader (0 without one).

        An unreadable indicator (a tooltip or an overlay over it) keeps the
        last floor: a guessed 0 has no map and lost the position for good.
        """
        if self._floor_reader is None:
            return 0
        region = self._floor_region
        if region is None and isinstance(self._floor_reader, FloorReader):
            region = self._floor_reader.floor_region
        floor = self._floor_reader.read_floor(frame, region)
        return self._position.z if floor is None else int(floor)

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
        r = max(2, round(2 * self._scale))
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
