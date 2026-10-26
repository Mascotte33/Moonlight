"""Status icon reading (H1.F1.K1).

Finds which condition icons (poison, burn, ...) are shown in the client's
status icon row by template matching.

# doc: docs/prs/healing.md#status-icons
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from moonlight.calibration.calibrate import CalibrationProfile

__all__ = ["CONDITION_TEMPLATES_DIR", "MATCH_THRESHOLD", "StatusIconReader"]

CONDITION_TEMPLATES_DIR = Path("data/templates/conditions")
CONDITIONS = ("poison", "burn", "electrify", "paralyse", "bleeding")

#: Largest mean squared colour difference per mask pixel that counts as a match.
#: A live real icon scores 0 and non-matches score over 800.
MATCH_THRESHOLD = 200.0


class StatusIconReader:
    """Reads the active conditions from the ``status_icons`` region."""

    def __init__(
        self,
        profile: CalibrationProfile,
        templates_dir: Path = CONDITION_TEMPLATES_DIR,
    ) -> None:
        self._region = profile.get_region("status_icons").to_tuple
        self._tiles: dict[str, tuple[np.ndarray, np.ndarray, float]] = {}
        for name in CONDITIONS:
            path = templates_dir / f"{name}.png"
            bgra = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if bgra is None or bgra.ndim != 3 or bgra.shape[2] != 4:
                raise FileNotFoundError(f"missing or non-RGBA condition template: {path}")
            rgb = np.ascontiguousarray(bgra[:, :, 2::-1]).astype(np.float32)
            weight = bgra[:, :, 3].astype(np.float32) / 255.0
            mask = np.repeat(weight[:, :, None], 3, axis=2)
            self._tiles[name] = (rgb, mask, float(weight.sum()))

    def read_active_conditions(self, frame: np.ndarray) -> frozenset[str]:
        """Names of the condition icons visible in the *frame* (RGB)."""
        x, y, w, h = self._region
        crop = frame[y : y + h, x : x + w].astype(np.float32)
        active = set()
        for name, (rgb, mask, weight_sum) in self._tiles.items():
            if crop.shape[0] < rgb.shape[0] or crop.shape[1] < rgb.shape[1]:
                raise ValueError(f"status_icons region {self._region} is smaller than a tile")
            scores = cv2.matchTemplate(crop, rgb, cv2.TM_SQDIFF, mask=mask)
            if scores.min() / weight_sum < MATCH_THRESHOLD:
                active.add(name)
        return frozenset(active)
