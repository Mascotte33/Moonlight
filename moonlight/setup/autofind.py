"""Find the screen areas on a screenshot by themselves (CALIBRATION.MD, C2).

One finder per area in :data:`FINDERS`. A finder takes a BGR screenshot and
returns the area's box, or ``None`` when it can't find it.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from moonlight.setup.calibrate import Rect

Finder = Callable[[np.ndarray], Rect | None]

FINDERS: list[tuple[str, Finder]] = []


def find_regions(
    screenshot: np.ndarray, finders: list[tuple[str, Finder]] | None = None
) -> tuple[dict[str, Rect], list[str]]:
    """Run every finder; return the found boxes and the names not found."""
    found: dict[str, Rect] = {}
    missing: list[str] = []
    for name, finder in FINDERS if finders is None else finders:
        try:
            box = finder(screenshot)
        except Exception:
            box = None
        if box is None:
            missing.append(name)
        else:
            found[name] = box
    return found, missing
