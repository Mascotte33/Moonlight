"""Mouse-cursor position reading (T17).

Detects where the game's *rendered* mouse cursor is on screen via
template matching. This is the verification half of the closed-loop click
path: after a USB HID move the bot must read back the actual cursor
position (the game snaps/quantizes the cursor, so the sent delta is not
trustworthy) before clicking (T17).

Layout facts:

* The Tibia cursor is one small arrow sprite rendered at the current
  mouse position. There is exactly one instance on screen at a time
  (unlike monsters), so there is no NMS step — only the single best
  match is reported.
* The search area is the *cursor region*: a rectangle of the client
  window where the cursor may appear (normally the whole window).

Implementation notes
--------------------

* Detection is **template matching** via :class:`cv2.matchTemplate`
  (``TM_CCOEFF_NORMED``) of grayscale cursor templates over the cropped
  cursor region — same technique as :mod:`moonlight.perception.sprites`
  and :mod:`moonlight.perception.floor`.
* Templates are small grayscale ``(H, W)`` crops of the rendered cursor.
  Real templates come from user screenshots (T08); synthetic templates
  are fine for the offline unit tests.
* :meth:`CursorReader.read` returns the **center** of the best match in
  frame (screen-window) coordinates — the anchor the game places at the
  logical mouse position — or ``None`` when no match reaches
  ``match_threshold`` (fail closed: no position, never a fabricated one).
* Frames are RGB ``(H, W, 3)`` ``uint8`` arrays as produced by
  ``moonlight.perception.capture``. Matching runs on the small region crop, never on
  the full frame (zero disk I/O, in-memory numpy only).
"""

from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np

__all__ = [
    "Rect",
    "DEFAULT_MATCH_THRESHOLD",
    "CursorTemplates",
    "CursorReader",
]

#: A rectangle as ``(x, y, width, height)`` (matches ``calibrate.Rect``).
Rect = tuple[int, int, int, int]

#: Minimum normalized match confidence to report a cursor position.
DEFAULT_MATCH_THRESHOLD = 0.80


class CursorTemplates:
    """Grayscale templates of the rendered mouse cursor.

    The cursor can render in a couple of states (e.g. the normal arrow
    and a right-click variant); a position is reported when *any*
    template matches. Templates are small grayscale ``(H, W)`` crops of
    the cursor taken from user screenshots (T08); synthetic templates are
    fine for offline tests.
    """

    def __init__(self, frames: Sequence[np.ndarray]) -> None:
        """Create a template set.

        Args:
            frames: One or more grayscale ``(H, W)`` template images.

        Raises:
            ValueError: If *frames* is empty, a frame is not a 2-D
                grayscale image, or a frame is empty (zero pixels).
        """
        if not frames:
            raise ValueError("Cursor templates require at least one frame")
        for frame in frames:
            if frame.ndim != 2:
                raise ValueError("Cursor templates must be 2-D grayscale (H, W)")
            if frame.size == 0:
                raise ValueError("Cursor templates must be non-empty (H, W) grayscale crops")
        self.frames: tuple[np.ndarray, ...] = tuple(frames)


class CursorReader:
    """Read the rendered mouse-cursor position from captured frames.

    Construct with the cursor region (explicitly, or from a
    :class:`moonlight.setup.calibrate.CalibrationProfile` carrying a
    ``cursor`` region) and the cursor templates. :meth:`read` then
    returns the center of the best template match in frame coordinates,
    or ``None``.

    Example:
        >>> reader = CursorReader(
        ...     cursor_region=(0, 0, 800, 600),
        ...     cursor_templates=[cursor_frame],
        ... )
        >>> frame = capture.get_frame()            # RGB (H, W, 3)
        >>> pos = reader.read(frame)               # (x, y) or None
    """

    def __init__(
        self,
        profile: object | None = None,
        *,
        cursor_region: Rect | None = None,
        cursor_templates: Sequence[np.ndarray] | np.ndarray | None = None,
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> None:
        """Create a reader.

        Args:
            profile: Optional ``CalibrationProfile``. Its ``cursor``
                region (if present) is used as the default region.
            cursor_region: Explicit cursor region; overrides the profile.
            cursor_templates: One or more grayscale cursor templates; a
                single bare 2-D array is accepted as one template.
            match_threshold: Minimum normalized match score to report a
                position (TM_CCOEFF_NORMED range -1..1).

        Raises:
            ValueError: If a region is not a 4-element tuple or the
                templates are invalid.
        """
        self._cursor_region: Rect | None = None
        if profile is not None:
            regions = getattr(profile, "regions", None)
            if regions:
                cursor = regions.get("cursor")
                if cursor is not None:
                    self._cursor_region = self._as_rect(cursor.to_tuple)

        if cursor_region is not None:
            self._cursor_region = self._as_rect(cursor_region)

        self._templates: CursorTemplates | None = None
        if cursor_templates is not None:
            if isinstance(cursor_templates, np.ndarray):
                cursor_templates = [cursor_templates]
            self._templates = CursorTemplates(cursor_templates)

        self._match_threshold = float(match_threshold)

    # -- public API ---------------------------------------------------------

    @property
    def cursor_region(self) -> Rect | None:
        """The stored cursor region (default for :meth:`read`)."""
        return self._cursor_region

    def read(
        self,
        frame: np.ndarray,
        region: Rect | None = None,
    ) -> tuple[int, int] | None:
        """Return the cursor position ``(x, y)`` in frame coordinates.

        Args:
            frame: RGB ``(H, W, 3)`` ``uint8`` capture.
            region: Cursor region to search; defaults to the constructor
                region.

        Returns:
            The **center** of the best template match in frame
            coordinates (the anchor the game places at the logical mouse
            position), or ``None`` when no region is known, no templates
            are available, the region is out of bounds, or no match
            reaches the threshold (detection failure — callers should
            fail closed).
        """
        region = region if region is not None else self._cursor_region
        if region is None or self._templates is None:
            return None
        crop, x0, y0 = self._crop(frame, region)
        if crop is None or crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        best_score = -1.0
        best_pos: tuple[int, int] | None = None
        for template in self._templates.frames:
            if template.shape[0] > gray.shape[0] or template.shape[1] > gray.shape[1]:
                continue
            result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            score = float(max_val)
            if score > best_score:
                best_score = score
                best_pos = (
                    max_loc[0] + template.shape[1] // 2,
                    max_loc[1] + template.shape[0] // 2,
                )
        if best_pos is None or best_score < self._match_threshold:
            return None
        return (best_pos[0] + x0, best_pos[1] + y0)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _crop(frame: np.ndarray, region: Rect) -> tuple[np.ndarray | None, int, int]:
        """Clip *region* to the frame bounds.

        Returns ``(crop, offset_x, offset_y)`` where *crop* is a view of the
        frame and the offset maps crop coordinates back to frame
        coordinates (the *clipped* origin, as in ``sprites`` — so regions
        that extend past the frame edge still report correct positions).
        Returns ``(None, 0, 0)`` when the region is empty or entirely out
        of range.
        """
        h, w = frame.shape[:2]
        x, y, rw, rh = region
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(w, int(x) + int(rw))
        y1 = min(h, int(y) + int(rh))
        if x1 <= x0 or y1 <= y0:
            return None, 0, 0
        return frame[y0:y1, x0:x1, :3], x0, y0

    @staticmethod
    def _as_rect(rect: object) -> Rect:
        """Validate and coerce a region to a ``Rect`` (as in vitals.py)."""
        if not isinstance(rect, Sequence) or isinstance(rect, (str, bytes)):
            raise ValueError(f"Region must be a 4-element sequence, got {rect!r}")
        if len(rect) != 4:
            raise ValueError(f"Region must have 4 values, got {len(rect)}")
        return (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
