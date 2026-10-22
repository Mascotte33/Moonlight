"""The steps of one calibration run, without any drawing.

doc: docs/prs/gui-calibration.md#calibration-steps

Boxes here are in screenshot pixels. They become landmark-relative only when
a profile is built.
"""

from __future__ import annotations

from moonlight.setup.calibrate import (
    REGIONS,
    CalibrationProfile,
    Point,
    Rect,
    Region,
    region_to_relative,
)


class CalibrationSteps:
    """Walk the areas of :data:`REGIONS` one by one, then a review of all of them."""

    def __init__(
        self,
        window_size: tuple[int, int],
        found: dict[str, Rect],
        old: CalibrationProfile | None = None,
        landmark: Point = (0, 0),
        ui_scale: float = 1.0,
    ) -> None:
        self.window_size = window_size
        self.boxes: dict[str, Rect] = {n: found[n] for n in REGIONS if n in found}
        self.confirmed: set[str] = set()
        self.old = old
        self.landmark = landmark
        self.ui_scale = ui_scale
        self.index = 0  # len(REGIONS) = the review
        self._back_to_review = False

    @property
    def in_review(self) -> bool:
        return self.index == len(REGIONS)

    @property
    def area(self) -> str | None:
        return None if self.in_review else REGIONS[self.index]

    @property
    def box(self) -> Rect | None:
        return None if self.area is None else self.boxes.get(self.area)

    def redraw(self, box: Rect) -> None:
        """Replace the current area's box (it still needs :meth:`keep`)."""
        if self.area is not None and box[2] > 0 and box[3] > 0:
            self.boxes[self.area] = box

    def keep(self) -> bool:
        """Confirm the current box and move on. False when there is no box yet."""
        if self.area is None or self.box is None:
            return False
        self.confirmed.add(self.area)
        if self._back_to_review:
            self._back_to_review = False
            self.index = len(REGIONS)
        else:
            self.index += 1
        return True

    def back(self) -> None:
        """Go to the previous area (from the review: the last area)."""
        self._back_to_review = False
        self.index = max(0, self.index - 1)

    def pick(self, name: str) -> None:
        """From the review, fix one area and come back to the review after."""
        if self.in_review and name in REGIONS:
            self.index = REGIONS.index(name)
            self._back_to_review = True

    def profile(self) -> CalibrationProfile:
        """The finished calibration: every area's box, the new size and scale."""
        if not self.in_review:
            raise ValueError("not every area is done yet")
        return self._build(self.boxes)

    def partial_profile(self) -> CalibrationProfile:
        """Confirmed boxes, plus the old profile's boxes for the rest."""
        boxes = {n: self.boxes[n] for n in self.confirmed}
        if self.old is not None:
            for name, region in self.old.regions.items():
                boxes.setdefault(name, region.absolute(self.old.landmark))
        return self._build(boxes)

    def _build(self, boxes: dict[str, Rect]) -> CalibrationProfile:
        return CalibrationProfile(
            landmark=self.landmark,
            regions={
                n: Region.from_tuple(region_to_relative(b, self.landmark))
                for n, b in boxes.items()
            },
            window_size=self.window_size,
            ui_scale=self.ui_scale,
        )
