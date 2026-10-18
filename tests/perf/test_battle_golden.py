"""Golden-frame accuracy tests for moonlight.perception.battle (T19/T08).

These tests compare :class:`moonlight.perception.battle.BattleReader`
output against human-labelled golden screenshots:

* frames:    ``data/golden/*.png`` (preferred, per T12 spec) or
             ``tests/golden/*.png`` (the default output of
             ``tools/label_golden.py``)
* labels:    ``tests/golden/labels.json`` or ``data/golden/labels.json``
             (produced by ``tools/label_golden.py``; stores
             ``monster_count``, ``selected_target`` and
             ``players_on_screen_count`` per frame)
* region:    the calibrated ``battle_list`` rect from
             ``profiles/<name>.json`` (produced by ``tools/calibrate.py``)

Label mapping (0-based, list order):

* ``monster_count``: number of non-player entries in the battle list
* ``selected_target``: index of the selected monster within the monster
  entries, or null when nothing is selected
* ``players_on_screen_count``: number of player entries in the list

IMPORTANT: no golden data exists yet (T08 pending -- the user will provide
screenshots). Until then, every test in this module skips cleanly. We
deliberately do NOT fabricate golden images or fake OCR results; the skip
logic below is the only thing that runs without data. When real frames
arrive, a template-based NameResolver must be wired into BattleReader and
the player-count semantics (own character appears in the list) confirmed
with the user's screenshots.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

import pytest
from moonlight.perception.battle import BattleEntry

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Where golden frames may live (T12 spec lists data/golden/ first).
GOLDEN_DIRS = (
    REPO_ROOT / "data" / "golden",
    REPO_ROOT / "tests" / "golden",
)

#: Where the labels file may live.
LABELS_CANDIDATES = (
    REPO_ROOT / "tests" / "golden" / "labels.json",
    REPO_ROOT / "data" / "golden" / "labels.json",
)

PROFILE_DIR = REPO_ROOT / "profiles"

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}


class FrameLabels(TypedDict, total=False):
    """Battle-relevant fields of one golden frame label (label_golden.py)."""

    monster_count: int
    selected_target: int | None
    players_on_screen_count: int


def _golden_dir() -> Path | None:
    """Return the first golden dir that contains at least one image."""
    for d in GOLDEN_DIRS:
        if not d.is_dir():
            continue
        if any(p.suffix.lower() in _IMAGE_SUFFIXES for p in d.iterdir()):
            return d
    return None


def _labels_path() -> Path | None:
    """Return the first existing labels.json."""
    for p in LABELS_CANDIDATES:
        if p.is_file():
            return p
    return None


def _usable_profile() -> object | None:
    """Return the first profile that has a calibrated battle_list rect."""
    if not PROFILE_DIR.is_dir():
        return None
    from moonlight.calibrate import CalibrationProfile

    for path in sorted(PROFILE_DIR.glob("*.json")):
        try:
            profile = CalibrationProfile.load(path)
        except Exception:
            continue
        if "battle_list" in profile.regions:
            return profile
    return None


def _skip_reason() -> str | None:
    """Compute why golden tests must skip, or None when they can run."""
    if _golden_dir() is None:
        return (
            "no golden frames yet (data/golden/ or tests/golden/ is empty or "
            "missing) -- T08: user will provide screenshots"
        )
    if _labels_path() is None:
        return (
            "golden frames present but no labels.json yet -- run "
            "tools/label_golden.py to label them"
        )
    if _usable_profile() is None:
        return (
            "golden frames and labels present but no profile with a "
            "battle_list rect -- run tools/calibrate.py first"
        )
    return None


_SKIP_REASON = _skip_reason()

_SKIP_DECORATOR = pytest.mark.skipif(
    _SKIP_REASON is not None,
    reason=_SKIP_REASON or "",
)


def _cases() -> list[tuple[str, Path, FrameLabels]]:
    """Return (name, image_path, frame_label) cases with battle labels."""
    golden_dir = _golden_dir()
    labels_path = _labels_path()
    assert golden_dir is not None  # guaranteed by skipif
    assert labels_path is not None  # guaranteed by skipif

    doc = json.loads(labels_path.read_text(encoding="utf-8"))
    frames = doc.get("frames", {})
    cases: list[tuple[str, Path, FrameLabels]] = []
    for name, entry in frames.items():
        image = golden_dir / name
        if image.is_file() and isinstance(entry, dict):
            cases.append((name, image, cast(FrameLabels, entry)))
    return cases


@_SKIP_DECORATOR
class TestBattleGolden:
    """Accuracy of BattleReader on labelled golden frames (T08 data)."""

    def test_frames_loaded(self) -> None:
        """Sanity check: at least one labelled golden frame is loadable."""
        import cv2

        cases = _cases()
        assert cases, "labels.json exists but no matching golden images found"
        for name, image, _ in cases:
            frame = cv2.imread(str(image))
            assert frame is not None, f"could not decode golden frame {name!r}"

    @staticmethod
    def _entries(frame: Path) -> list[BattleEntry]:
        """Read battle-list entries from a BGR golden frame via the profile."""
        import cv2
        from moonlight.perception.battle import BattleReader

        profile = _usable_profile()
        assert profile is not None  # guaranteed by skipif
        bgr = cv2.imread(str(frame))
        assert bgr is not None, f"could not decode golden frame {frame!r}"
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return BattleReader(profile).read_battle_list(rgb)

    def test_monster_count(self) -> None:
        """Non-player entry count matches the labelled monster_count."""
        for name, image, label in _cases():
            expected = label.get("monster_count")
            if expected is None:
                continue
            entries = self._entries(image)
            monsters = [e for e in entries if not e.is_player]
            assert len(monsters) == int(expected), (
                f"{name!r}: {len(monsters)} monster entries but label says {expected}"
            )

    def test_selected_target(self) -> None:
        """The labelled selected target is the flagged monster entry."""
        for name, image, label in _cases():
            expected = label.get("selected_target")
            entries = self._entries(image)
            monsters = [e for e in entries if not e.is_player]
            if expected is None:
                assert all(not e.is_selected for e in entries), (
                    f"{name!r}: an entry is selected but label says none"
                )
                continue
            selected = [e for e in entries if e.is_selected]
            assert len(selected) == 1, (
                f"{name!r}: expected exactly 1 selected entry, got {len(selected)}"
            )
            monster_index = [m.index for m in monsters].index(selected[0].index)
            assert monster_index == int(expected), (
                f"{name!r}: selected monster at index {monster_index} but label says {expected}"
            )

    def test_players_on_screen_count(self) -> None:
        """Player entry count matches the labelled players_on_screen_count."""
        for name, image, label in _cases():
            expected = label.get("players_on_screen_count")
            if expected is None:
                continue
            entries = self._entries(image)
            players = [e for e in entries if e.is_player]
            assert len(players) == int(expected), (
                f"{name!r}: {len(players)} player entries but label says {expected}"
            )
