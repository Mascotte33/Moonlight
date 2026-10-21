"""Golden-frame accuracy tests for moonlight.perception.floor (T21/T08).

These tests compare :class:`moonlight.perception.floor.FloorReader` output
against human-labelled golden screenshots:

* frames:    ``data/golden/*.png`` (preferred, per T12 spec) or
             ``tests/golden/*.png`` (the default output of
             ``tools/label_golden.py``)
* labels:    ``tests/golden/labels.json`` or ``data/golden/labels.json``
             (``position_z`` 0-15 per frame; null = unlabelled, skipped)
* regions:   the calibrated ``floor_indicator`` rect from
             ``moonlight/profiles/<name>.json`` (produced by ``tools/calibrate.py``)
* templates: ``<level>.png`` icons in ``data/templates/floorLevels/``

Accuracy target: exact level match (template matching is categorical).

Status (T28): the 200 frames, labels and the ``floor_indicator`` rect (the
level slider right of the minimap) are present, but no floor templates exist
and every frame's ``position_z`` is null, so this module skips with that
reason. We deliberately do NOT fabricate labels or templates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonlight.setup.calibrate import default_profiles_dir

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

#: Same directory the app loads profiles from (moonlight/profiles/).
PROFILE_DIR = default_profiles_dir()

#: Where per-level floor icons may live.
TEMPLATE_DIR = REPO_ROOT / "data" / "templates" / "floorLevels"

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}


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
    """Return the first profile that has a calibrated floor_indicator rect."""
    if not PROFILE_DIR.is_dir():
        return None
    from moonlight.setup.calibrate import CalibrationProfile

    for path in sorted(PROFILE_DIR.glob("*.json")):
        try:
            profile = CalibrationProfile.load(path)
        except Exception:
            continue
        if "floor_indicator" in profile.regions:
            return profile
    return None


def _templates_present() -> bool:
    """True when at least one template icon exists on disk."""
    if not TEMPLATE_DIR.is_dir():
        return False
    return any(p.suffix.lower() in _IMAGE_SUFFIXES for p in TEMPLATE_DIR.iterdir())


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
            "floor_indicator rect -- run tools/calibrate.py first"
        )
    if not _templates_present():
        return (
            "floor templates missing (data/templates/floorLevels/) -- "
            "provide the 0-15 level icons first"
        )
    return None


_SKIP_REASON = _skip_reason()

_SKIP_DECORATOR = pytest.mark.skipif(
    _SKIP_REASON is not None,
    reason=_SKIP_REASON or "",
)


@_SKIP_DECORATOR
class TestFloorGolden:
    """Accuracy of FloorReader on labelled golden frames (T08 data)."""

    @staticmethod
    def _cases() -> list[tuple[str, Path, int]]:
        """Return (name, image_path, expected_floor) cases."""
        golden_dir = _golden_dir()
        labels_path = _labels_path()
        assert golden_dir is not None  # guaranteed by skipif
        assert labels_path is not None  # guaranteed by skipif

        frames = json.loads(labels_path.read_text(encoding="utf-8"))["frames"]
        cases: list[tuple[str, Path, int]] = []
        for name, entry in frames.items():
            image = golden_dir / name
            floor = entry.get("position_z")
            if image.is_file() and floor is not None:
                cases.append((name, image, int(floor)))
        return cases

    def test_frames_loaded(self) -> None:
        """Sanity check: at least one labelled golden frame is loadable."""
        import cv2

        cases = self._cases()
        if not cases:
            pytest.skip("no frame has a labelled position_z")
        for name, image, _ in cases:
            frame = cv2.imread(str(image))
            assert frame is not None, f"could not decode golden frame {name!r}"

    def test_floor_accuracy(self) -> None:
        """read_floor matches the labelled level for every golden frame."""
        import cv2

        from moonlight.perception.floor import FloorReader

        cases = self._cases()
        if not cases:
            pytest.skip("no frame has a labelled position_z")
        reader = FloorReader(_usable_profile())
        for name, image, expected in cases:
            bgr = cv2.imread(str(image))
            assert bgr is not None, f"could not decode golden frame {name!r}"
            frame = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            got = reader.read_floor(frame)
            assert got == expected, (
                f"{name!r}: read_floor={got} but label says {expected}"
            )
