"""HP/MP reading (T12).

Extracts HP% and MP% from a captured Tibia client frame by counting the
colour-filled pixels of the status-bar widgets.

How it works
------------
HP and MP are horizontal bars with a fixed width. Each bar is drawn as:

* a **fill** colour (HP red / MP blue on the 1.2+ widget; on the current
  client HP is green/yellow-green/yellow/dark-red by health and MP is dark
  blue), and
* an **empty/background** grey filling the remainder.

The current client fills HP from the left but MP from the *right*, and draws
white ``cur/max`` text over both. So we count fill *columns* (a column is
filled if any pixel in it matches a fill colour), which is independent of
fill direction and of text covering some rows::

    percent = filled_columns / bar_width * 100

(see ``docs/ui_layout.md`` sections 1 & 2; client palette measured on the
T08 golden frames).

We do NOT hard-code absolute pixel coordinates: the caller passes a
calibrated region ``Rect = (x, y, w, h)`` (from ``calibrate.py`` /
``profiles/<name>.json``). :class:`VitalsReader` can be built from a
:class:`moonlight.setup.calibrate.CalibrationProfile` so the ``hp_bar`` /
``mp_bar`` regions are picked up automatically, while still allowing each
``read_*`` call to override the region explicitly.

Colour detection uses a per-channel tolerance (default +/-20) to absorb
screenshot/video compression artifacts. Detection is fully vectorised with
NumPy so a read on a 1080p-derived region crop is well under the 10 ms step
budget (see ``docs/hw_budget.md``).

Only pixels are read; this module never writes and never needs OpenCV, a
display, or a live client.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

__all__ = [
    "Rect",
    "RGB",
    "HP_FILL_RGB",
    "MP_FILL_RGB",
    "HP_FILL_COLOURS",
    "MP_FILL_COLOURS",
    "BAR_BACKGROUND_RGB",
    "DEFAULT_TOLERANCE",
    "VitalsReader",
    "bar_percent",
]

#: A rectangle as ``(x, y, width, height)`` (matches ``calibrate.Rect``).
Rect = tuple[int, int, int, int]

#: An RGB triple.
RGB = tuple[int, int, int]

#: HP bar fill colour (1.2+ widget) -- RGB (241, 97, 97). See ui_layout.md #1.
HP_FILL_RGB: RGB = (241, 97, 97)

#: MP bar fill colour (1.2+ widget) -- RGB (83, 80, 218). See ui_layout.md #2.
MP_FILL_RGB: RGB = (83, 80, 218)

#: Every HP fill colour read_hp accepts: the 1.2+ widget colour plus the
#: current client's health stages (mid-bar row, measured on T08 golden frames).
HP_FILL_COLOURS: tuple[RGB, ...] = (
    HP_FILL_RGB,
    (0, 181, 0),  # green (high HP)
    (104, 151, 4),  # yellow-green
    (190, 145, 8),  # yellow
    (90, 0, 0),  # dark red (low HP)
)

#: Every MP fill colour read_mp accepts (1.2+ widget + current client).
#: The client MP gradient needs two refs: the upper rows are the only ones
#: never hidden by text or the purple magic-shield overlay.
MP_FILL_COLOURS: tuple[RGB, ...] = (MP_FILL_RGB, (0, 70, 155), (0, 54, 120))

#: Bar empty/background grey. See ui_layout.md #1/#2.
BAR_BACKGROUND_RGB: RGB = (95, 95, 95)

#: Default per-channel colour tolerance to absorb compression artifacts.
DEFAULT_TOLERANCE = 20


def _clip_region(frame: np.ndarray, region: Rect) -> tuple[np.ndarray, int] | None:
    """Clip *region* to the frame bounds and return ``(crop, width)``.

    Returns ``None`` if the region is empty or lies entirely outside the
    frame. The crop is a view (no copy), so this is cheap.
    """
    h, w = frame.shape[:2]
    x, y, rw, rh = region

    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(w, int(x) + int(rw))
    y1 = min(h, int(y) + int(rh))

    if x1 <= x0 or y1 <= y0:
        return None

    crop = frame[y0:y1, x0:x1, :3]
    return crop, (x1 - x0)


def bar_percent(
    frame: np.ndarray,
    region: Rect,
    fill_rgb: RGB | Sequence[RGB],
    tolerance: int = DEFAULT_TOLERANCE,
) -> float:
    """Return the filled percentage of a single horizontal bar in 0..100.

    Args:
        frame: RGB ``(H, W, 3)`` ``uint8`` capture (as produced by
            ``moonlight.perception.capture``).
        region: Bar region ``(x, y, w, h)`` in frame coordinates.
        fill_rgb: The fill colour of the bar (e.g. :data:`HP_FILL_RGB`), or
            several accepted colours (e.g. :data:`HP_FILL_COLOURS`).
        tolerance: Per-channel colour tolerance for compression artifacts.

    Returns:
        The filled percentage as a float in ``[0.0, 100.0]``. Returns ``0.0``
        for empty/invalid regions or when no bar colour is detected.
    """
    clipped = _clip_region(frame, region)
    if clipped is None:
        return 0.0
    crop, width = clipped

    # Every row of the region is read: text and the magic-shield overlay can
    # hide the middle rows, so the edge rows are needed. ponytail: assumes the
    # region is calibrated tight to the bar; UI pixels inside a loose region
    # that happen to match a fill colour would be counted.
    refs = np.array(fill_rgb, dtype=np.int16).reshape(-1, 1, 1, 3)
    diff = np.abs(crop.astype(np.int16)[None] - refs)
    fill_mask = (diff.max(axis=3) <= tolerance).any(axis=0)  # per-pixel bool

    # A column is filled if any row matches: independent of fill direction
    # and of overlaid text hiding some rows.
    filled = float(fill_mask.any(axis=0).sum())

    percent = 100.0 * filled / width
    return max(0.0, min(100.0, percent))


class VitalsReader:
    """Read HP% and MP% from captured frames using calibrated regions.

    Construct from a :class:`moonlight.setup.calibrate.CalibrationProfile` (which
    stores the ``hp_bar`` / ``mp_bar`` regions) and/or explicit region
    tuples. The stored regions are the defaults used when ``read_*`` is
    called without an explicit region.

    Example:
        >>> profile = CalibrationProfile.load("profiles/default.json")
        >>> reader = VitalsReader(profile)
        >>> frame = capture.get_frame()            # RGB (H, W, 3)
        >>> hp = reader.read_hp(frame)             # uses profile hp_bar
        >>> mp = reader.read_mp(frame)             # uses profile mp_bar
    """

    def __init__(
        self,
        profile: object | None = None,
        *,
        hp_region: Rect | None = None,
        mp_region: Rect | None = None,
        tolerance: int = DEFAULT_TOLERANCE,
    ) -> None:
        """Create a reader.

        Args:
            profile: Optional ``CalibrationProfile``. Its ``hp_bar`` and
                ``mp_bar`` regions are used as defaults.
            hp_region: Explicit HP region; overrides the profile.
            mp_region: Explicit MP region; overrides the profile.
            tolerance: Per-channel colour tolerance.

        Raises:
            ValueError: If a region is not a 4-element tuple.
        """
        self.tolerance = int(tolerance)
        self._hp_region: Rect | None = None
        self._mp_region: Rect | None = None

        if profile is not None:
            regions = getattr(profile, "regions", None)
            if regions:
                hp = regions.get("hp_bar")
                mp = regions.get("mp_bar")
                if hp is not None:
                    self._hp_region = self._as_rect(hp.to_tuple)
                if mp is not None:
                    self._mp_region = self._as_rect(mp.to_tuple)

        # Explicit regions take priority.
        if hp_region is not None:
            self._hp_region = self._as_rect(hp_region)
        if mp_region is not None:
            self._mp_region = self._as_rect(mp_region)

    # -- public API ---------------------------------------------------------

    def read_hp(
        self,
        frame: np.ndarray,
        region: Rect | None = None,
    ) -> float:
        """Return the HP percentage (0..100) from *frame*.

        Args:
            frame: RGB ``(H, W, 3)`` capture.
            region: HP region to use; defaults to the constructor region.
        """
        region = region if region is not None else self._hp_region
        if region is None:
            return 0.0
        return bar_percent(frame, region, HP_FILL_COLOURS, self.tolerance)

    def read_mp(
        self,
        frame: np.ndarray,
        region: Rect | None = None,
    ) -> float:
        """Return the MP percentage (0..100) from *frame*.

        Args:
            frame: RGB ``(H, W, 3)`` capture.
            region: MP region to use; defaults to the constructor region.
        """
        region = region if region is not None else self._mp_region
        if region is None:
            return 0.0
        return bar_percent(frame, region, MP_FILL_COLOURS, self.tolerance)

    def read_vitals(
        self,
        frame: np.ndarray,
        hp_region: Rect | None = None,
        mp_region: Rect | None = None,
    ) -> tuple[float, float]:
        """Return ``(hp_percent, mp_percent)`` in one call."""
        return (
            self.read_hp(frame, hp_region),
            self.read_mp(frame, mp_region),
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _as_rect(rect: object) -> Rect:
        """Validate and coerce a region to a ``Rect``.

        Accepts a 4-element sequence (e.g. ``Region.to_tuple``) and returns
        it as a ``tuple[int, int, int, int]``.
        """
        if not isinstance(rect, Sequence) or isinstance(rect, (str, bytes)):
            raise ValueError(f"Region must be a 4-element sequence, got {rect!r}")
        if len(rect) != 4:
            raise ValueError(f"Region must have 4 values, got {len(rect)}")
        return (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
