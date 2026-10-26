"""Loot window reading (T23).

Reads an open corpse/container window (see ``docs/ui_layout.md`` #7):

* The window is located by template-matching its title bar
  (:data:`TITLE_TEMPLATE`) inside the calibrated ``loot_window`` region.
* The item grid starts right below the title bar: ``columns`` x ``rows``
  equal slots of ``slot_size`` px. Each slot crop is matched against every
  item template; the best match above the threshold names the item.

Templates are grayscale ``(H, W)`` images keyed by name: ``"loot_window"``
for the title bar, anything else is an item. Until T08 ships real loot
templates the set is empty and :meth:`LootReader.read_loot_window` returns
``None`` (fail closed) — we never fabricate a window or items.

Item counts (the digits drawn on stacked items) are not read yet: every
detected item reports ``count=1``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import cv2
import numpy as np

from moonlight.perception.floor import DEFAULT_MATCH_THRESHOLD, Rect, scale_image

__all__ = [
    "TITLE_TEMPLATE",
    "LootItem",
    "LootWindow",
    "LootReader",
]

#: Template key for the loot window's title bar.
TITLE_TEMPLATE = "loot_window"

#: (slot_index, item_name, count)
LootItem = tuple[int, str, int]


@dataclass(frozen=True)
class LootWindow:
    """One read of the loot window.

    ``capacity_percent`` is not visible in the loot window itself; the
    reader leaves it at 0.0 (unknown) and the caller may fill it from
    ``WorldState.capacity``.
    """

    open: bool
    items: list[LootItem] = field(default_factory=list)
    capacity_percent: float = 0.0


class LootReader:
    """Detect the loot window and the items in its grid by templates."""

    def __init__(
        self,
        templates: Mapping[str, np.ndarray],
        profile: object | None = None,
        *,
        region: Rect | None = None,
        columns: int = 5,
        rows: int = 5,
        slot_size: int = 34,  # ~30-45 px per ui_layout.md #7; confirm with T08 screenshots
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> None:
        """Create a reader.

        Args:
            templates: Grayscale templates by name (see the module docstring).
            profile: Optional ``CalibrationProfile``; its ``loot_window``
                region is the search area.
            region: Explicit search area; overrides the profile.
            columns: Slots per grid row.
            rows: Grid rows.
            slot_size: Slot pitch in pixels.
            match_threshold: Minimum TM_CCOEFF_NORMED score.
        """
        for name, image in templates.items():
            if image.ndim != 2:
                raise ValueError(f"Template {name!r} must be 2-D grayscale")
        scale = float(getattr(profile, "ui_scale", 1.0))
        templates = {k: scale_image(v, scale) for k, v in templates.items()}
        self._title = templates.get(TITLE_TEMPLATE)
        self._items = {k: v for k, v in templates.items() if k != TITLE_TEMPLATE}
        self._region = region
        if self._region is None and profile is not None:
            loot = (getattr(profile, "regions", None) or {}).get("loot_window")
            if loot is not None:
                self._region = loot.to_tuple
        self._columns = columns
        self._rows = rows
        self._slot = round(slot_size * scale)
        self._threshold = float(match_threshold)

    def read_loot_window(self, frame: np.ndarray) -> LootWindow | None:
        """Return the open loot window, or ``None`` when none is visible.

        Also ``None`` when no region or no title template is available.
        """
        if self._region is None or self._title is None:
            return None
        h, w = frame.shape[:2]
        x, y, rw, rh = self._region
        x0, y0, x1, y1 = max(0, x), max(0, y), min(w, x + rw), min(h, y + rh)
        th, tw = self._title.shape
        if y1 - y0 < th or x1 - x0 < tw:
            return None
        gray = cv2.cvtColor(frame[y0:y1, x0:x1, :3], cv2.COLOR_RGB2GRAY)
        result = cv2.matchTemplate(gray, self._title, cv2.TM_CCOEFF_NORMED)
        _, score, _, (tx, ty) = cv2.minMaxLoc(result)
        if score < self._threshold:
            return None
        return LootWindow(open=True, items=self._read_items(gray, tx, ty + th))

    def _read_items(self, gray: np.ndarray, gx: int, gy: int) -> list[LootItem]:
        """Match every item template in each grid slot below the title."""
        items: list[LootItem] = []
        if not self._items:
            return items
        s = self._slot
        for index in range(self._columns * self._rows):
            row, col = divmod(index, self._columns)
            cell = gray[gy + row * s : gy + (row + 1) * s, gx + col * s : gx + (col + 1) * s]
            if cell.shape != (s, s) or cell.std() == 0:  # off-screen or empty slot
                continue
            best_name, best_score = None, self._threshold
            for name, tmpl in self._items.items():
                if tmpl.shape[0] > s or tmpl.shape[1] > s:
                    continue
                score = float(cv2.matchTemplate(cell, tmpl, cv2.TM_CCOEFF_NORMED).max())
                if score >= best_score:
                    best_name, best_score = name, score
            if best_name is not None:
                items.append((index, best_name, 1))
        return items
