"""F-18: the golden-label loader refuses placeholder labels loudly."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.golden_labels import PlaceholderLabelsError, load_golden_labels, unlabelled_modules

PLACEHOLDER: dict[str, Any] = {
    "hp_percent": 100.0,
    "mp_percent": 100.0,
    "monster_count": 0,
    "selected_target": None,
    "players_on_screen_count": 0,
    "position_x": None,
    "position_y": None,
    "position_z": None,
}


def write(tmp_path: Path, *frames: dict[str, Any]) -> Path:
    path = tmp_path / "labels.json"
    doc = {"version": 1, "frames": {f"f{i}.png": f for i, f in enumerate(frames)}}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_placeholder_labels_fail_listing_unlabelled_modules(tmp_path: Path) -> None:
    # Measured HP/MP (as calibrate_auto writes), everything else the defaults.
    path = write(tmp_path, {**PLACEHOLDER, "hp_percent": 78.4}, PLACEHOLDER)
    with pytest.raises(PlaceholderLabelsError) as exc:
        load_golden_labels(path, require=("vitals", "battle", "minimap", "floor"))
    msg = str(exc.value)
    assert "battle, minimap, floor" in msg
    assert "vitals" not in msg
    assert "tools/label_golden.py" in msg


def test_labelled_modules_load(tmp_path: Path) -> None:
    # One frame with a monster is enough: a 0 elsewhere is then a real label.
    path = write(tmp_path, {**PLACEHOLDER, "monster_count": 2}, PLACEHOLDER)
    frames = load_golden_labels(path, require=("battle",))
    assert len(frames) == 2
    assert unlabelled_modules(frames) == ["vitals", "minimap", "floor"]


def test_repo_labels_report_their_placeholders() -> None:
    """The shipped labels.json: measured vitals, placeholder battle/position (T33)."""
    path = Path(__file__).resolve().parents[0] / "golden" / "labels.json"
    if not path.is_file():
        pytest.skip("no tests/golden/labels.json")
    frames = load_golden_labels(path, require=("vitals",))
    assert "vitals" not in unlabelled_modules(frames)
