"""T28 replay: every perception reader over the 200 golden frames.

Frames are read from ``golde-screenshots/`` (falling back to
``tests/golden/``); labels from ``tests/golden/labels.json``. The reader
APIs are the real ones (see tests/perf/test_golden_smoke.py for the template
cuts used by the unlabelled readers). Labels carry no sprite/loot/log truth
and only placeholder battle data (monster_count 0), so those readers get
validity + no-exception checks, plus label-conditional checks that become
active once real battle labels exist.

``test_fast_subset`` (first 10 frames) is the quick gate; the full run is
``@pytest.mark.slow``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from moonlight.bot.state import Position
from moonlight.perception import (
    BattleEntry,
    BattleReader,
    CursorReader,
    FloorReader,
    LogFrame,
    LogOCR,
    LogOCRConfig,
    MinimapReader,
    SpriteDetection,
    SpriteDetector,
    VitalsReader,
)
from moonlight.perception.loot import TITLE_TEMPLATE, LootReader, LootWindow
from moonlight.setup.calibrate import CalibrationProfile, default_profiles_dir
from tests.golden_labels import PlaceholderLabelsError, load_golden_labels

ROOT = Path(__file__).resolve().parents[2]
FRAME_DIRS = (ROOT / "golde-screenshots", ROOT / "tests" / "golden")
LABELS = ROOT / "tests" / "golden" / "labels.json"
PROFILE = default_profiles_dir() / "moonlight_user.json"

GAME_GRID = (416, 60, 1442, 1056)
LOG_REGION = (180, 1210, 2020, 135)
SPRITE_CUT = (1105, 556, 64, 64)
CURSOR_CUT = (2520, 20, 16, 16)
LOOT_TITLE_CUT = (0, 0, 120, 16)
LOOT_ITEM_CUT = (10, 40, 20, 20)
FAST_N = 10
# Measured: real players move up to ~8 tiles between burst frames, so the
# briefed +/-3 does not hold. The tracker itself rejects shifts > radar/2.
MINIMAP_MAX_STEP = 53


def _frame_paths() -> list[Path]:
    for d in FRAME_DIRS:
        found = sorted(p for p in d.glob("*.png")) if d.is_dir() else []
        if found:
            return found
    return []


PATHS = _frame_paths()
pytestmark = pytest.mark.skipif(
    not PATHS or not PROFILE.is_file(), reason="no golden frames or profile"
)


def _session(path: Path) -> str:
    """Capture session = ``<date>_<time>_<char>_<event>`` without the frame index."""
    return path.stem.rsplit("_", 1)[0] if path.stem[-1].isdigit() else path.stem


def _cut(rgb: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = rect
    return cv2.cvtColor(rgb[y : y + h, x : x + w], cv2.COLOR_RGB2GRAY).copy()


def _load(path: Path) -> np.ndarray:
    bgr = cv2.imread(str(path))
    assert bgr is not None, f"cannot decode {path.name}"
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _make_readers(first: np.ndarray) -> dict[str, Callable[[np.ndarray], Any]]:
    profile = CalibrationProfile.load(PROFILE)
    vitals = VitalsReader(profile)
    log, log_cfg = LogOCR(), LogOCRConfig(log_region=LOG_REGION, enabled=True)
    return {
        "vitals": vitals.read_vitals,
        "battle": BattleReader(profile).read_battle_list,
        "floor": FloorReader(profile).read_floor,
        "cursor": CursorReader(
            cursor_region=(0, 0, first.shape[1], first.shape[0]),
            cursor_templates=_cut(first, CURSOR_CUT),
        ).read,
        "sprites": SpriteDetector(
            game_grid_region=GAME_GRID, monster_templates={"probe": [_cut(first, SPRITE_CUT)]}
        ).detect,
        "loot": LootReader(
            {TITLE_TEMPLATE: _cut(first, LOOT_TITLE_CUT), "probe": _cut(first, LOOT_ITEM_CUT)},
            region=(0, 0, 400, 400),
        ).read_loot_window,
        "logocr": lambda f: log.read(f, log_cfg),
    }


def _run(paths: list[Path]) -> dict[str, list[Any]]:
    """Run every reader on every frame; any exception fails the test."""
    first = _load(paths[0])
    readers = _make_readers(first)
    # Minimap tracks relative motion: fresh reader per capture session.
    profile = CalibrationProfile.load(PROFILE)
    minimap, last_session = MinimapReader(profile), None
    out: dict[str, list[Any]] = {k: [] for k in (*readers, "minimap")}
    for p in paths:
        frame = first if p == paths[0] else _load(p)
        for name, read in readers.items():
            out[name].append(read(frame))
        if _session(p) != last_session:
            minimap.reset()
            last_session = _session(p)
        out["minimap"].append(minimap.read_position(frame))
    return out


def _same_session_pairs(paths: list[Path]) -> list[tuple[int, int]]:
    return [
        (i, i + 1) for i in range(len(paths) - 1) if _session(paths[i]) == _session(paths[i + 1])
    ]


def _check(paths: list[Path], out: dict[str, list[Any]]) -> None:
    h, w = _load(paths[0]).shape[:2]
    # F-18: the HP/MP gate below needs measured vitals labels, never placeholders.
    labels = load_golden_labels(LABELS, require=("vitals",)) if LABELS.is_file() else {}
    pairs = _same_session_pairs(paths)

    for p, (hp, mp) in zip(paths, out["vitals"], strict=True):
        assert 0.0 <= hp <= 100.0 and 0.0 <= mp <= 100.0
        lab = labels.get(p.name, {})
        if not str(lab.get("notes", "")).startswith("layout-mismatch") and "hp_percent" in lab:
            assert abs(hp - lab["hp_percent"]) <= 2.0, p.name
            assert abs(mp - lab["mp_percent"]) <= 2.0, p.name
    # Frame-to-frame stability is NOT asserted: HP/MP legitimately jump between
    # consecutive frames of a session (labels show 100 -> 78 -> 100).

    for p, entries in zip(paths, out["battle"], strict=True):
        assert all(isinstance(e, BattleEntry) for e in entries)
        if "BossDefeated" in p.name:
            assert not any(e.is_selected for e in entries), p.name
        lab = labels.get(p.name, {})
        if lab.get("monster_count", 0) > 0:  # placeholder zeros today -> inactive
            assert any(not e.is_player for e in entries), p.name

    for pos in out["minimap"]:
        assert pos is None or isinstance(pos, Position)
    for i, j in pairs:
        a, b = out["minimap"][i], out["minimap"][j]
        if a and b:
            assert abs(a.x - b.x) <= MINIMAP_MAX_STEP and abs(a.y - b.y) <= MINIMAP_MAX_STEP, paths[
                j
            ].name

    for f in out["floor"]:
        assert f is None or 0 <= f <= 16
    for i, j in pairs:
        a, b = out["floor"][i], out["floor"][j]
        if a is not None and b is not None:
            assert a == b, paths[j].name

    for c in out["cursor"]:
        assert c is None or (0 <= c[0] < w and 0 <= c[1] < h)

    for p, hits in zip(paths, out["sprites"], strict=True):
        assert all(isinstance(s, SpriteDetection) for s in hits)
        if labels.get(p.name, {}).get("monster_count", 0) > 0:
            assert hits, p.name

    for log in out["logocr"]:
        assert isinstance(log, LogFrame)
        assert 0.0 <= log.confidence <= 1.0

    loot = out["loot"]
    assert all(x is None or isinstance(x, LootWindow) for x in loot)
    # "Rare window" is not asserted: no real loot-window template is in the repo, and
    # the probe cut (frame 0's top-left corner) matches every frame.


def test_label_gated_checks_have_labels() -> None:
    """F-18: the battle/sprite/minimap checks in _check are label-gated.

    Refuse to call them covered while labels.json holds placeholders: skip
    loudly, naming the modules to label (T33, human).
    """
    if not LABELS.is_file():
        pytest.skip("no tests/golden/labels.json")
    try:
        load_golden_labels(LABELS, require=("battle", "minimap", "floor"))
    except PlaceholderLabelsError as exc:
        pytest.skip(f"golden gate refused: {exc}")


def test_fast_subset() -> None:
    paths = PATHS[:FAST_N]
    _check(paths, _run(paths))


@pytest.mark.slow
def test_full_replay() -> None:
    _check(PATHS, _run(PATHS))
