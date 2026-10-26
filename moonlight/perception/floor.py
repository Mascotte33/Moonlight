"""Floor indicator reading (T21/T23).

Detects the current z-level (floor) from the Tibia client's floor indicator,
which sits in the minimap area (see ``docs/ui_layout.md`` sections 4 & 5).

Layout facts (measured from PyTibia):

* 16 possible floor levels, indexed 0–15 (:data:`FLOOR_LEVELS`).
* Convention: ``0`` = highest (sky/roof), ``7`` = **ground floor**
  (:data:`GROUND_FLOOR`), ``15`` = deepest underground.
* Display method: a small indicator icon is matched against 16 reference
  templates (PyTibia ships ``floorLevels/<level>.png``); the highest-scoring
  match is the current floor.

Implementation notes
--------------------

* Detection is **template matching** via :class:`cv2.matchTemplate`
  (``TM_CCOEFF_NORMED``) of each level's grayscale template over the
  calibrated indicator region. No OCR, no heuristics.
* Templates are loaded lazily from ``data/templates/floorLevels`` by default
  (overridable). Until the user provides real screenshots (T08), the
  directory does not exist and :meth:`FloorReader.read_floor` returns
  ``None`` (fail closed) — we never fabricate a floor.
* A :class:`TemplateSet` can be injected directly (used by the synthetic
  unit tests); this keeps the matching path fully testable offline.
* Frames are RGB ``(H, W, 3)`` ``uint8`` arrays as produced by
  ``moonlight.perception.capture``.

Performance: 16 normalized matches over a small indicator region are well
under the 10 ms per-read budget (T30).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np

__all__ = [
    "Rect",
    "MIN_FLOOR",
    "MAX_FLOOR",
    "FLOOR_LEVELS",
    "GROUND_FLOOR",
    "DEFAULT_TEMPLATE_DIR",
    "DEFAULT_MATCH_THRESHOLD",
    "TemplateSet",
    "FloorReader",
    "scale_image",
]

#: A rectangle as ``(x, y, width, height)`` (matches ``calibrate.Rect``).
Rect = tuple[int, int, int, int]

#: Lowest floor index (highest physical level: sky/roof).
MIN_FLOOR = 0

#: Highest floor index (deepest underground level).
MAX_FLOOR = 15

#: All 16 floor indices, 0..15.
FLOOR_LEVELS: tuple[int, ...] = tuple(range(MIN_FLOOR, MAX_FLOOR + 1))

#: Ground (street) floor index. See ui_layout.md #5.
GROUND_FLOOR = 7

#: Default directory holding ``<level>.png`` templates (0..15).
DEFAULT_TEMPLATE_DIR = "data/templates/floorLevels"

#: Minimum normalized match confidence for a floor to be reported.
DEFAULT_MATCH_THRESHOLD = 0.80


# doc: docs/prs/gui-calibration.md#ui-scale
def scale_image(image: np.ndarray, scale: float) -> np.ndarray:
    """Resize a template by the profile's ``ui_scale``; 1.0 returns it as is."""
    if scale == 1.0:
        return image
    h, w = image.shape[:2]
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    # ponytail: nearest keeps pixel art crisp; switch to linear if a smooth-scaled client misses
    return cv2.resize(image, size, interpolation=cv2.INTER_NEAREST)


class TemplateSet:
    """Grayscale floor templates: one image per level index.

    Templates are small crops of the indicator icon taken on each floor
    (see ``docs/ui_layout.md`` #5). Matching compares the current indicator
    region against every template and picks the highest-scoring one.
    """

    def __init__(self, templates: dict[int, np.ndarray]) -> None:
        """Create a template set.

        Args:
            templates: Mapping of floor level -> grayscale ``(H, W)`` image.
                Levels outside 0..15 are rejected.

        Raises:
            ValueError: If *templates* is empty or contains an invalid level
                or a non-2D grayscale image.
        """
        if not templates:
            raise ValueError("TemplateSet requires at least one template")
        for level, image in templates.items():
            if not MIN_FLOOR <= level <= MAX_FLOOR:
                raise ValueError(f"Floor level must be {MIN_FLOOR}..{MAX_FLOOR}, got {level}")
            if image.ndim != 2:
                raise ValueError(f"Template for level {level} must be 2-D grayscale")
        self._templates = dict(templates)

    @classmethod
    def load(cls, directory: str | Path) -> TemplateSet | None:
        """Load ``<level>.png`` templates from *directory* (PyTibia style).

        Args:
            directory: Directory containing ``0.png`` .. ``15.png``.

        Returns:
            A :class:`TemplateSet` for the levels that decode, or ``None``
            when the directory does not exist or no template decodes
            (callers should fail closed with ``None`` floor reads).
        """
        path = Path(directory)
        if not path.is_dir():
            return None
        templates: dict[int, np.ndarray] = {}
        for level in FLOOR_LEVELS:
            image = cv2.imread(str(path / f"{level}.png"), cv2.IMREAD_GRAYSCALE)
            if image is not None:
                templates[level] = image
        if not templates:
            return None
        return cls(templates)

    def scaled(self, scale: float) -> TemplateSet:
        """Return this set with every template resized by *scale*."""
        if scale == 1.0:
            return self
        return TemplateSet({k: scale_image(v, scale) for k, v in self._templates.items()})

    def levels(self) -> tuple[int, ...]:
        """Return the sorted levels that have a template."""
        return tuple(sorted(self._templates))

    def match(
        self,
        roi_gray: np.ndarray,
        threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> tuple[int, float] | None:
        """Template-match *roi_gray* against every template.

        Args:
            roi_gray: Grayscale indicator region from the frame.
            threshold: Minimum normalized confidence to accept a match.

        Returns:
            ``(level, confidence)`` for the best template, or ``None`` when
            no template reaches *threshold* (detection failure). Templates
            that do not fit inside the region are skipped.
        """
        if roi_gray.ndim != 2 or roi_gray.size == 0:
            return None
        best_level: int | None = None
        best_score = -1.0
        for level, template in self._templates.items():
            if template.shape[0] > roi_gray.shape[0] or template.shape[1] > roi_gray.shape[1]:
                continue
            result = cv2.matchTemplate(roi_gray, template, cv2.TM_CCOEFF_NORMED)
            score = float(result.max())
            if score > best_score:
                best_score = score
                best_level = level
        if best_level is None or best_score < threshold:
            return None
        return best_level, best_score


class FloorReader:
    """Read the current floor level (0–15) from captured frames.

    Construct from a :class:`moonlight.setup.calibrate.CalibrationProfile` (which
    stores the ``floor_indicator`` region) and/or an explicit region tuple.
    Templates are loaded lazily from *template_dir* on the first read unless
    a :class:`TemplateSet` is injected.

    Example:
        >>> profile = CalibrationProfile.load("profiles/default.json")
        >>> reader = FloorReader(profile)
        >>> frame = capture.get_frame()            # RGB (H, W, 3)
        >>> floor = reader.read_floor(frame)       # int 0..15 or None
    """

    def __init__(
        self,
        profile: object | None = None,
        *,
        floor_region: Rect | None = None,
        template_set: TemplateSet | None = None,
        template_dir: str | Path = DEFAULT_TEMPLATE_DIR,
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> None:
        """Create a reader.

        Args:
            profile: Optional ``CalibrationProfile``. Its
                ``floor_indicator`` region is used as the default region.
            floor_region: Explicit indicator region; overrides the profile.
            template_set: Pre-built templates; bypasses *template_dir*.
            template_dir: Directory with ``<level>.png`` templates.
            match_threshold: Minimum match confidence to report a floor.

        Raises:
            ValueError: If a region is not a 4-element tuple.
        """
        self._floor_region: Rect | None = None
        if profile is not None:
            regions = getattr(profile, "regions", None)
            if regions:
                floor = regions.get("floor_indicator")
                if floor is not None:
                    self._floor_region = self._as_rect(floor.to_tuple)

        if floor_region is not None:
            self._floor_region = self._as_rect(floor_region)

        self._scale = float(getattr(profile, "ui_scale", 1.0))
        self._templates = None if template_set is None else template_set.scaled(self._scale)
        self._template_dir = template_dir
        self._templates_tried = template_set is not None
        self._match_threshold = float(match_threshold)

    # -- public API ---------------------------------------------------------

    @property
    def floor_region(self) -> Rect | None:
        """The stored indicator region (default for :meth:`read_floor`)."""
        return self._floor_region

    def read_floor(
        self,
        frame: np.ndarray,
        region: Rect | None = None,
    ) -> int | None:
        """Return the current floor level (0..15) from *frame*.

        Args:
            frame: RGB ``(H, W, 3)`` ``uint8`` capture.
            region: Floor-indicator region; defaults to the constructor
                region.

        Returns:
            The floor index, or ``None`` when no region is known, the region
            is out of bounds, no templates are available, or no template
            matches confidently (detection failure).
        """
        region = region if region is not None else self._floor_region
        if region is None:
            return None
        crop = self._crop(frame, region)
        if crop is None or crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        templates = self._templates_lazy()
        if templates is None:
            return None
        match = templates.match(gray, self._match_threshold)
        if match is None:
            return None
        level, _score = match
        return level

    # -- helpers ------------------------------------------------------------

    def _templates_lazy(self) -> TemplateSet | None:
        """Load templates from *template_dir* once, then cache the result."""
        if self._templates_tried:
            return self._templates
        loaded = TemplateSet.load(self._template_dir)
        self._templates = None if loaded is None else loaded.scaled(self._scale)
        self._templates_tried = True
        return self._templates

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
