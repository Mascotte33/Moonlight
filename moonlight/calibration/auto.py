"""Calibration on Start: reuse the saved layout while it fits, else find it again.

# doc: docs/prs/auto-calibration.md#on-start

Start reads the client once. While the saved calibration still fits (same
window size, the Stop button where it was) it is kept. Otherwise every area
is searched again and the result saved, so the owner never calibrates by
hand. Areas the bot cannot run without stop the Start with a message naming
what to open in the client.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import numpy as np

from moonlight.calibration.autofind import find_regions, find_ui_scale
from moonlight.calibration.calibrate import (
    OPTIONAL_REGIONS,
    REGIONS,
    CalibrationProfile,
    Region,
)

__all__ = ["AREA_NAMES", "CalibrationMissingError", "calibrate", "missing_message"]

#: What the owner sees for an area that is not on screen.
AREA_NAMES: Mapping[str, str] = {
    "hp_bar": "HP bar",
    "mp_bar": "Mana bar",
    "battle_list": "Battle List",
    "minimap": "Minimap",
    "floor_indicator": "Floor indicator",
    "status_icons": "Condition icons",
    "game_grid": "Game view",
    "chase_buttons": "Chase buttons",
    "player_list": "Players window",
    "potions": "Potion counts on the action bar",
    "cap": "Cap box",
    "level": "Skills window (level)",
}


class CalibrationMissingError(ValueError):
    """Areas the bot needs are not on screen; the message names them."""


def missing_message(missing: list[str]) -> str:
    """``"Battle List, Cap box not found: open them in Tibia"``."""
    names = ", ".join(AREA_NAMES.get(n, n) for n in missing)
    them = "it" if len(missing) == 1 else "them"
    return f"{names} not found: open {them} in Tibia"


def calibrate(
    frame_bgr: np.ndarray, old: CalibrationProfile | None
) -> tuple[CalibrationProfile, list[str], list[str]]:
    """Find every area on *frame_bgr* (the client as BGR).

    Returns the calibration, the required areas not found and the optional
    ones not found. A required area not found keeps nothing from *old*: the
    layout changed, so its old box would read the wrong pixels.
    """
    found, _ = find_regions(frame_bgr)
    scale, landmark = find_ui_scale(frame_bgr)
    if landmark == (0, 0) and old is not None:
        scale = old.ui_scale  # no Stop button found: keep the old scale, not a guessed 1.0
    regions = {n: Region.from_tuple(found[n]) for n in REGIONS + OPTIONAL_REGIONS if n in found}
    missing = [n for n in REGIONS if n not in found]
    optional = [n for n in OPTIONAL_REGIONS if n not in found]
    size = (frame_bgr.shape[1], frame_bgr.shape[0])
    return CalibrationProfile(landmark, regions, size, scale), missing, optional


def calibrate_and_save(
    frame_rgb: np.ndarray,
    path: Path,
    old: CalibrationProfile | None,
    log: Callable[[str], None],
) -> CalibrationProfile:
    """Calibrate on *frame_rgb*, save it to *path* and return it.

    Raises:
        CalibrationMissingError: When a required area is not on screen
            (nothing is saved).
    """
    profile, missing, optional = calibrate(frame_rgb[..., ::-1], old)
    if missing:
        raise CalibrationMissingError(missing_message(missing))
    path.parent.mkdir(parents=True, exist_ok=True)
    profile.save(path)
    log(f"calibration: found the client layout, saved to {path}")
    if optional:
        log(f"calibration: {missing_message(optional)} (optional, its features stay off)")
    return profile
