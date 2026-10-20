"""Golden-label loader that refuses placeholder labels (F-18).

``tests/golden/labels.json`` was produced by ``tools/calibrate_auto.py``,
which measures only HP/MP and writes the ``tools/label_golden.py`` defaults
for every other field. A module whose fields hold those defaults on *every*
frame has no ground truth: asserting against it is vacuous. Golden gates
call :func:`load_golden_labels` with the modules they need and get a loud
:class:`PlaceholderLabelsError` naming the unlabelled ones instead of a
silently green run.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

#: Label fields per perception module, with their placeholder (default) values.
MODULE_PLACEHOLDERS: dict[str, dict[str, Any]] = {
    "vitals": {"hp_percent": 100.0, "mp_percent": 100.0},
    "battle": {"monster_count": 0, "selected_target": None, "players_on_screen_count": 0},
    "minimap": {"position_x": None, "position_y": None},
    "floor": {"position_z": None},
}


class PlaceholderLabelsError(RuntimeError):
    """Required golden labels are placeholders, not measured truth."""


def unlabelled_modules(frames: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Modules whose fields hold the placeholder on every frame (or no frames)."""
    return [
        module
        for module, fields in MODULE_PLACEHOLDERS.items()
        if all(
            all(frame.get(k, default) == default for k, default in fields.items())
            for frame in frames.values()
        )
    ]


def load_golden_labels(path: Path, require: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Return the ``frames`` of *path*; raise if a *require*d module is unlabelled.

    Raises:
        PlaceholderLabelsError: listing every required module that only has
            placeholder labels, and how to label it.
    """
    frames: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["frames"]
    missing = [m for m in require if m in unlabelled_modules(frames)]
    if missing:
        raise PlaceholderLabelsError(
            f"{path.name} holds only placeholder labels for: {', '.join(missing)} "
            f"({len(frames)} frames) -- label them with tools/label_golden.py before "
            "trusting this golden gate"
        )
    return frames
