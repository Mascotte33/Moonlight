"""Monster sprite detection (T17/T23).

Finds monster sprites in the Tibia client's game field by template
matching: each known monster has one or more sprite templates (its
animation frames), and :meth:`SpriteDetector.detect` locates them in a
captured frame with :func:`cv2.matchTemplate`.

Why this exists
---------------
The battle list (``battle.py``) tells the bot *which* monsters are present
(names, HP) but not *where*. Closed-loop clicks (T17) and looting (T23)
need the sprite's screen coordinates. This module bridges that gap: given
the calibrated ``game_grid`` region and per-monster sprite templates, it
returns each visible sprite's position in frame coordinates.

Implementation notes
--------------------

* Detection is **template matching** (``TM_CCOEFF_NORMED``) of each
  monster's grayscale templates over the grayscale game-grid crop — the
  same technique ``floor.py`` uses for the floor indicator. No OCR, no
  heuristics.
* Only the ``game_grid`` region crop is ever touched (never full frames),
  keeping a per-read cost far below the 10 ms perception budget (T30).
* **On demand only (P3).** Nothing calls this on the tick; a caller runs
  it when it needs a sprite position. ``near_player=True`` searches only
  the central half of the grid (~1/4 of the pixels) where the fight is,
  and *monsters* limits the search to the names the battle list shows.
  At most ``max_templates`` template frames are matched per call.
* **Coarse-to-fine (P3).** Matching runs first on a ``downscale``-x
  smaller image (``downscale**2`` fewer pixels) with a lenient
  threshold; each coarse peak is then re-matched at full resolution in a
  few-pixel window, so reported positions and confidences are exact and
  the real threshold still decides.
* Matches below *match_threshold* are discarded. Overlapping candidates
  (e.g. two animation frames of the same monster matching the same
  sprite) are collapsed with non-maximum suppression (IoU > *nms_iou*
  keeps only the higher-confidence candidate), so a sprite is never
  reported twice.
* Templates are 2-D grayscale images (``H, W``), as produced by
  ``cv2.imread(..., cv2.IMREAD_GRAYSCALE)``. Real templates come from
  user screenshots (T08); until then, synthetic templates keep this
  module fully testable offline.
* Frames are RGB ``(H, W, 3)`` ``uint8`` arrays as produced by
  ``moonlight.perception.capture``.

Reported positions are the top-left corners of the matched sprites in
*frame* (screen-window) coordinates, already offset by the region's
position — ready for the decision layer.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from moonlight.perception.floor import scale_image

__all__ = [
    "Rect",
    "DEFAULT_MATCH_THRESHOLD",
    "DEFAULT_NMS_IOU",
    "SpriteDetection",
    "MonsterTemplates",
    "SpriteDetector",
]

#: A rectangle as ``(x, y, width, height)`` (matches ``calibrate.Rect``).
Rect = tuple[int, int, int, int]

#: Minimum normalized match confidence to report a sprite (as in floor.py).
DEFAULT_MATCH_THRESHOLD = 0.80

#: IoU above which two candidate matches are the same sprite (NMS).
DEFAULT_NMS_IOU = 0.5

#: Coarse-pass shrink factor (P3); 1 disables the coarse pass.
DEFAULT_DOWNSCALE = 2

#: Most template frames matched per :meth:`SpriteDetector.detect` call (P3).
DEFAULT_MAX_TEMPLATES = 8

#: Templates whose downscaled side would drop below this skip the coarse pass.
_MIN_COARSE_SIDE = 6


@dataclass(frozen=True)
class SpriteDetection:
    """One sprite detected in the game grid.

    Attributes:
        monster: Name of the matched monster.
        x: Top-left x of the match in frame (screen-window) coordinates.
        y: Top-left y of the match in frame (screen-window) coordinates.
        width: Matched template width in pixels.
        height: Matched template height in pixels.
        confidence: Normalized match score in (0.0, 1.0].
    """

    monster: str
    x: int
    y: int
    width: int
    height: int
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        """Center of the match in frame coordinates (for clicks, T17)."""
        return (self.x + self.width // 2, self.y + self.height // 2)

    @property
    def to_rect(self) -> Rect:
        """The match as ``(x, y, width, height)`` in frame coordinates."""
        return (self.x, self.y, self.width, self.height)


class MonsterTemplates:
    """Sprite templates for one monster.

    A monster ships multiple template frames (animation states: standing,
    attacking, ...). A sprite is considered this monster when *any* of its
    templates matches — each frame is matched independently.

    Templates are small grayscale ``(H, W)`` crops of the sprite taken from
    user screenshots (T08); synthetic templates are fine for offline tests.
    """

    def __init__(self, name: str, frames: Sequence[np.ndarray]) -> None:
        """Create a template set.

        Args:
            name: Monster name (must be non-empty).
            frames: One or more grayscale ``(H, W)`` template images.

        Raises:
            ValueError: If *name* is empty, *frames* is empty, or a frame
                is not a 2-D grayscale image.
        """
        if not name:
            raise ValueError("Monster templates require a non-empty name")
        if not frames:
            raise ValueError(f"Monster {name!r} requires at least one template frame")
        for frame in frames:
            if frame.ndim != 2:
                raise ValueError(
                    f"Template for monster {name!r} must be 2-D grayscale"
                )
        self.name = name
        self.frames: tuple[np.ndarray, ...] = tuple(frames)


class SpriteDetector:
    """Locate monster sprites in the game grid via template matching.

    Construct with the game-grid region (explicitly or from a
    :class:`moonlight.calibration.calibrate.CalibrationProfile`) and the per-monster
    sprite templates. :meth:`detect` then returns one
    :class:`SpriteDetection` per visible sprite.

    Example:
        >>> profile = CalibrationProfile.load("profiles/default.json")
        >>> detector = SpriteDetector(
        ...     profile,
        ...     monster_templates={
        ...         "wolf": [wolf_frame_1, wolf_frame_2],
        ...     },
        ... )
        >>> frame = capture.get_frame()            # RGB (H, W, 3)
        >>> for hit in detector.detect(frame):
        ...     print(hit.monster, hit.center, hit.confidence)
    """

    def __init__(
        self,
        profile: object | None = None,
        *,
        game_grid_region: Rect | None = None,
        monster_templates: (
            Mapping[str, Sequence[np.ndarray]]
            | Sequence[MonsterTemplates]
            | None
        ) = None,
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
        nms_iou: float = DEFAULT_NMS_IOU,
        downscale: int = DEFAULT_DOWNSCALE,
        max_templates: int = DEFAULT_MAX_TEMPLATES,
    ) -> None:
        """Create a detector.

        Args:
            profile: Optional ``CalibrationProfile``. Its ``game_grid``
                region is used as the default region.
            game_grid_region: Explicit game-grid region; overrides the
                profile.
            monster_templates: Per-monster sprite templates, either as a
                ``{monster: [frame, ...]}`` mapping or as a sequence of
                :class:`MonsterTemplates`.
            match_threshold: Minimum normalized match score to report a
                sprite (TM_CCOEFF_NORMED range -1..1).
            nms_iou: Two candidates with IoU strictly above this are the
                same sprite; only the higher-confidence one is reported.
            downscale: Coarse-pass shrink factor (1 = full resolution only).
            max_templates: Most template frames matched per call.

        Raises:
            ValueError: If a region is not a 4-element tuple, a template
                set is invalid, or *nms_iou* is outside (0.0, 1.0].
        """
        self._game_grid_region: Rect | None = None
        if profile is not None:
            regions = getattr(profile, "regions", None)
            if regions:
                grid = regions.get("game_grid")
                if grid is not None:
                    self._game_grid_region = self._as_rect(grid.to_tuple)

        if game_grid_region is not None:
            self._game_grid_region = self._as_rect(game_grid_region)

        if nms_iou <= 0.0 or nms_iou > 1.0:
            raise ValueError(f"nms_iou must be in (0.0, 1.0], got {nms_iou}")
        if downscale < 1 or max_templates < 1:
            raise ValueError("downscale and max_templates must be >= 1")

        self._monsters: list[MonsterTemplates] = []
        if monster_templates is not None:
            if isinstance(monster_templates, Mapping):
                for name, frames in monster_templates.items():
                    self._monsters.append(MonsterTemplates(str(name), frames))
            else:
                self._monsters = list(monster_templates)
        scale = float(getattr(profile, "ui_scale", 1.0))
        if scale != 1.0:
            self._monsters = [
                MonsterTemplates(m.name, [scale_image(f, scale) for f in m.frames])
                for m in self._monsters
            ]

        self._match_threshold = float(match_threshold)
        self._nms_iou = float(nms_iou)
        self._downscale = int(downscale)
        self._max_templates = int(max_templates)
        # Coarse templates, computed once (None = too small, full-res only).
        self._coarse: dict[int, np.ndarray | None] = {}

    # -- public API ---------------------------------------------------------

    @property
    def game_grid_region(self) -> Rect | None:
        """The stored game-grid region (default for :meth:`detect`)."""
        return self._game_grid_region

    @property
    def monsters(self) -> tuple[str, ...]:
        """Names of the monsters that have sprite templates."""
        return tuple(m.name for m in self._monsters)

    def detect(
        self,
        frame: np.ndarray,
        region: Rect | None = None,
        *,
        monsters: Collection[str] | None = None,
        near_player: bool = False,
    ) -> list[SpriteDetection]:
        """Return the sprites visible in the game grid of *frame*.

        Args:
            frame: RGB ``(H, W, 3)`` ``uint8`` capture.
            region: Game-grid region; defaults to the constructor region.
            monsters: Only match these monsters (e.g. the battle-list
                names); None matches every configured monster.
            near_player: Search only the central half of the region
                (the player stands at its centre).

        Returns:
            One :class:`SpriteDetection` per visible sprite (positions in
            frame coordinates, sorted by confidence, highest first). Empty
            when no region is known, the region is out of bounds, no
            templates are configured, or nothing matches confidently.
        """
        region = region if region is not None else self._game_grid_region
        if region is None or not self._monsters:
            return []
        if near_player:
            x, y, w, h = region
            region = (x + w // 4, y + h // 4, w // 2, h // 2)
        crop, ox, oy = self._crop_with_offset(frame, region)
        if crop is None or crop.size == 0:
            return []
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        candidates = self._find_candidates(gray, ox, oy, monsters)
        return self._suppress(candidates, self._nms_iou)

    # -- helpers ------------------------------------------------------------

    def _find_candidates(
        self,
        gray: np.ndarray,
        offset_x: int,
        offset_y: int,
        monsters: Collection[str] | None = None,
    ) -> list[SpriteDetection]:
        """Template-match the wanted monsters' frames over *gray*.

        Args:
            gray: Grayscale game-grid crop.
            offset_x: Crop's x offset in frame coordinates.
            offset_y: Crop's y offset in frame coordinates.
            monsters: Names to match; None matches every monster.

        Returns:
            All matches at or above the match threshold, with positions
            translated to frame coordinates. Templates that do not fit
            inside the crop are skipped.
        """
        candidates: list[SpriteDetection] = []
        jobs = [
            (monster.name, template)
            for monster in self._monsters
            if monsters is None or monster.name in monsters
            for template in monster.frames
        ][: self._max_templates]
        small: np.ndarray | None = None
        for name, template in jobs:
            if template.shape[0] > gray.shape[0] or template.shape[1] > gray.shape[1]:
                continue
            coarse = self._coarse_template(template)
            if coarse is None:
                hits = self._match(gray, template, self._match_threshold)
            else:
                if small is None:
                    small = self._shrink(gray)
                hits = self._refine(gray, template, small, coarse)
            for x, y, score in hits:
                candidates.append(
                    SpriteDetection(
                        monster=name,
                        x=offset_x + x,
                        y=offset_y + y,
                        width=template.shape[1],
                        height=template.shape[0],
                        confidence=score,
                    )
                )
        return candidates

    def _shrink(self, image: np.ndarray) -> np.ndarray:
        """Downscale *image* by ``downscale`` (area averaging)."""
        h, w = image.shape[:2]
        d = self._downscale
        return cv2.resize(image, (max(1, w // d), max(1, h // d)), interpolation=cv2.INTER_AREA)

    def _coarse_template(self, template: np.ndarray) -> np.ndarray | None:
        """Cached downscaled *template*, or None when it would be too small."""
        key = id(template)
        if key not in self._coarse:
            d = self._downscale
            too_small = d == 1 or min(template.shape[:2]) // d < _MIN_COARSE_SIDE
            self._coarse[key] = None if too_small else self._shrink(template)
        return self._coarse[key]

    @staticmethod
    def _match(
        gray: np.ndarray, template: np.ndarray, threshold: float
    ) -> list[tuple[int, int, float]]:
        """Every ``(x, y, score)`` where *template* scores >= *threshold*."""
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= threshold)
        return [
            (int(x), int(y), float(result[int(y), int(x)]))
            for y, x in zip(ys, xs, strict=False)
        ]

    def _refine(
        self,
        gray: np.ndarray,
        template: np.ndarray,
        small: np.ndarray,
        coarse: np.ndarray,
    ) -> list[tuple[int, int, float]]:
        """Coarse peaks on *small*, then a full-res re-match around each.

        The coarse threshold is lenient (half the real one) because a
        sprite off the shrink grid (odd offset) correlates less once
        downscaled; the full-res pass applies the real threshold, so
        nothing weaker than before is reported and positions/confidences
        are exact.

        ponytail: one sub-pixel phase only; fine for pixel-art sprites
        (features >= 2 px). Per-pixel-noise templates can miss at odd
        offsets -- use ``downscale=1`` for those.
        """
        if coarse.shape[0] > small.shape[0] or coarse.shape[1] > small.shape[1]:
            return self._match(gray, template, self._match_threshold)
        d = self._downscale
        th, tw = template.shape[:2]
        peaks = [
            (cx * d, cy * d, score)
            for cx, cy, score in self._match(small, coarse, self._match_threshold * 0.5)
        ]
        # Coarse NMS: neighbouring coarse hits share one refine window.
        peaks.sort(key=lambda p: p[2], reverse=True)
        kept: list[tuple[int, int]] = []
        for fx, fy, _ in peaks:
            if all(abs(fx - kx) >= tw // 2 or abs(fy - ky) >= th // 2 for kx, ky in kept):
                kept.append((fx, fy))
        out: dict[tuple[int, int], float] = {}
        pad = d + 1
        for fx, fy in kept:
            x0, y0 = max(0, fx - pad), max(0, fy - pad)
            x1 = min(gray.shape[1], fx + pad + tw)
            y1 = min(gray.shape[0], fy + pad + th)
            if x1 - x0 < tw or y1 - y0 < th:
                continue
            for x, y, score in self._match(gray[y0:y1, x0:x1], template, self._match_threshold):
                out[(x0 + x, y0 + y)] = score
        return [(x, y, score) for (x, y), score in out.items()]

    @staticmethod
    def _suppress(
        candidates: list[SpriteDetection], nms_iou: float
    ) -> list[SpriteDetection]:
        """Non-maximum suppression: collapse overlapping candidates.

        Candidates are scanned from strongest to weakest; a candidate is
        dropped when its IoU with any already-kept candidate exceeds
        *nms_iou* (e.g. two animation frames matching the same sprite).
        The survivors are returned sorted by confidence, highest first.
        """
        kept: list[SpriteDetection] = []
        for cand in sorted(candidates, key=lambda d: d.confidence, reverse=True):
            if all(_iou(cand, k) <= nms_iou for k in kept):
                kept.append(cand)
        return kept

    @staticmethod
    def _crop_with_offset(frame: np.ndarray, region: Rect) -> tuple[np.ndarray | None, int, int]:
        """Clip *region* to the frame bounds.

        Returns ``(crop, offset_x, offset_y)`` where *crop* is a view of
        the frame and the offset maps crop coordinates to frame
        coordinates. Returns ``(None, 0, 0)`` when the region is empty or
        lies entirely outside the frame.
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


def _iou(a: SpriteDetection, b: SpriteDetection) -> float:
    """Intersection-over-union of two detections' bounding boxes.

    Returns 0.0 when the boxes do not overlap.
    """
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x + a.width, b.x + b.width)
    y2 = min(a.y + a.height, b.y + b.height)
    inter_w = x2 - x1
    inter_h = y2 - y1
    if inter_w <= 0 or inter_h <= 0:
        return 0.0
    inter = float(inter_w) * float(inter_h)
    union = float(a.width * a.height + b.width * b.height - inter_w * inter_h)
    if union <= 0.0:
        return 0.0
    return inter / union
