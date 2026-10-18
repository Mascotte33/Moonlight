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
  ``moonlight.capture``.

Reported positions are the top-left corners of the matched sprites in
*frame* (screen-window) coordinates, already offset by the region's
position — ready for the decision layer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import cv2
import numpy as np

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
    :class:`moonlight.calibrate.CalibrationProfile`) and the per-monster
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

        self._monsters: list[MonsterTemplates] = []
        if monster_templates is not None:
            if isinstance(monster_templates, Mapping):
                for name, frames in monster_templates.items():
                    self._monsters.append(MonsterTemplates(str(name), frames))
            else:
                self._monsters = list(monster_templates)

        self._match_threshold = float(match_threshold)
        self._nms_iou = float(nms_iou)

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
    ) -> list[SpriteDetection]:
        """Return the sprites visible in the game grid of *frame*.

        Args:
            frame: RGB ``(H, W, 3)`` ``uint8`` capture.
            region: Game-grid region; defaults to the constructor region.

        Returns:
            One :class:`SpriteDetection` per visible sprite (positions in
            frame coordinates, sorted by confidence, highest first). Empty
            when no region is known, the region is out of bounds, no
            templates are configured, or nothing matches confidently.
        """
        region = region if region is not None else self._game_grid_region
        if region is None or not self._monsters:
            return []
        crop, ox, oy = self._crop_with_offset(frame, region)
        if crop is None or crop.size == 0:
            return []
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        candidates = self._find_candidates(gray, ox, oy)
        return self._suppress(candidates, self._nms_iou)

    # -- helpers ------------------------------------------------------------

    def _find_candidates(
        self,
        gray: np.ndarray,
        offset_x: int,
        offset_y: int,
    ) -> list[SpriteDetection]:
        """Template-match every monster frame over *gray*.

        Args:
            gray: Grayscale game-grid crop.
            offset_x: Crop's x offset in frame coordinates.
            offset_y: Crop's y offset in frame coordinates.

        Returns:
            All matches at or above the match threshold, with positions
            translated to frame coordinates. Templates that do not fit
            inside the crop are skipped.
        """
        candidates: list[SpriteDetection] = []
        for monster in self._monsters:
            for template in monster.frames:
                if template.shape[0] > gray.shape[0] or template.shape[1] > gray.shape[1]:
                    continue
                result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
                ys, xs = np.where(result >= self._match_threshold)
                for y, x in zip(ys, xs, strict=False):
                    candidates.append(
                        SpriteDetection(
                            monster=monster.name,
                            x=offset_x + int(x),
                            y=offset_y + int(y),
                            width=template.shape[1],
                            height=template.shape[0],
                            confidence=float(result[int(y), int(x)]),
                        )
                    )
        return candidates

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
