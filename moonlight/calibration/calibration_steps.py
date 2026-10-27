"""Calibration steps: walk the areas of ``AREAS`` with no drawing.

# doc: docs/prs/gui-calibration.md#calibration-steps
"""

from __future__ import annotations

from collections.abc import Mapping

from moonlight.calibration.calibrate import (
    OPTIONAL_REGIONS,
    REGIONS,
    CalibrationProfile,
    Point,
    Rect,
    Region,
)

#: The walk: the required areas, then the optional ones.
AREAS = REGIONS + OPTIONAL_REGIONS


# doc: docs/prs/gui-calibration.md#calibration-steps
class CalibrationSteps:
    """State machine for the Calibration tab: area by area, then a review."""

    def __init__(
        self,
        screenshot_size: tuple[int, int],
        found: Mapping[str, Rect],
        old_profile: CalibrationProfile | None = None,
        landmark: Point = (0, 0),
    ) -> None:
        self.screenshot_size = screenshot_size
        self.landmark = landmark
        self.ui_scale = 1.0
        self.old_profile = old_profile
        #: Box per area: found at first, replaced by redraws.
        self.boxes: dict[str, Rect] = {k: v for k, v in found.items() if k in AREAS}
        #: Areas the user kept or redrew this run.
        self.confirmed: set[str] = set()
        self._index = 0
        self._review = False
        self._from_review = False

    @property
    def in_review(self) -> bool:
        return self._review

    @property
    def area(self) -> str | None:
        """Current area name; None while in the review."""
        return None if self._review else AREAS[self._index]

    @property
    def box(self) -> Rect | None:
        """Box of the current area, or None."""
        return None if self._review else self.boxes.get(AREAS[self._index])

    @property
    def can_save(self) -> bool:
        return self._review and all(n in self.boxes for n in REGIONS)  # optional may be absent

    def keep(self) -> None:
        """Confirm the current box. Does nothing when there is no box."""
        if not self._review and self.box is not None:
            self.confirmed.add(AREAS[self._index])
            self._advance()

    def redraw(self, rect: Rect) -> None:
        """Replace the current box with a drawn one and confirm it."""
        if not self._review:
            name = AREAS[self._index]
            self.boxes[name] = rect
            self.confirmed.add(name)
            self._advance()

    def back(self) -> None:
        """Previous area; from a review jump, back to the review. First area: nothing."""
        if self._review:
            self._review, self._index = False, len(AREAS) - 1
        elif self._from_review:
            self._review = True
        elif self._index > 0:
            self._index -= 1

    def next(self) -> None:
        """Move to the next area without confirming. Not in the review or a review jump."""
        if not self._review and not self._from_review:
            if self._index < len(AREAS) - 1:
                self._index += 1
            else:  # past the last (optional) area: the review
                self._review = True

    def prev(self) -> None:
        """Move to the previous area without confirming. Same limits as :meth:`next`."""
        if not self._review and not self._from_review and self._index > 0:
            self._index -= 1

    def pick(self, name: str) -> None:
        """From the review, jump to one area; keep or redraw returns here."""
        if self._review and name in AREAS:
            self._index = AREAS.index(name)
            self._review = False
            self._from_review = True

    def save(self) -> CalibrationProfile:
        """Full save, only from the review with all areas present."""
        if not self.can_save:
            raise ValueError("full save needs the review with all areas set")
        return self._build(self.boxes)

    def save_partial(self) -> CalibrationProfile:
        """Confirmed boxes, plus the old profile's boxes for the rest."""
        regions = {n: self.boxes[n] for n in AREAS if n in self.confirmed}
        return self._build(regions)

    def _advance(self) -> None:
        if self._from_review or self._index == len(AREAS) - 1:
            self._review, self._from_review = True, False
        else:
            self._index += 1

    def _build(self, boxes: Mapping[str, Rect]) -> CalibrationProfile:
        old = self.old_profile.regions if self.old_profile else {}
        regions = {
            n: Region.from_tuple(boxes[n]) if n in boxes else old[n]
            for n in AREAS
            if n in boxes or n in old
        }
        return CalibrationProfile(self.landmark, regions, self.screenshot_size, self.ui_scale)
