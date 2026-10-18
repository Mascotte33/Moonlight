"""Golden-frame accuracy tests for moonlight.perception.vitals (T12/T08).

These tests compare :class:`moonlight.perception.vitals.VitalsReader`
output against human-labelled golden screenshots:

* frames:    ``data/golden/*.png`` (preferred, per T12 spec) or
             ``tests/golden/*.png`` (the default output of
             ``tools/label_golden.py``)
* labels:    ``tests/golden/labels.json`` or ``data/golden/labels.json``
             (produced by ``tools/label_golden.py``; stores ``hp_percent``
             / ``mp_percent`` per frame)
* regions:   the calibrated ``hp_bar`` / ``mp_bar`` rects from
             ``profiles/<name>.json`` (produced by ``tools/calibrate.py``)

Accuracy target: within +/-2 percentage points of the labelled value.

IMPORTANT: no golden data exists yet (T08 pending -- the user will provide
screenshots). Until then, every test in this module skips cleanly. We
deliberately do NOT fabricate golden images or fake percentages; the skip
logic below is the only thing that runs without data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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

#: Required accuracy: within +/-2 percentage points.
ACCURACY_TOLERANCE = 2.0

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
    """Return the first profile that has calibrated hp_bar and mp_bar rects."""
    if not PROFILE_DIR.is_dir():
        return None
    from moonlight.calibrate import CalibrationProfile

    for path in sorted(PROFILE_DIR.glob("*.json")):
        try:
            profile = CalibrationProfile.load(path)
        except Exception:
            continue
        if "hp_bar" in profile.regions and "mp_bar" in profile.regions:
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
            "golden frames and labels present but no profile with hp_bar and "
            "mp_bar rects -- run tools/calibrate.py first"
        )
    return None


_SKIP_REASON = _skip_reason()

_SKIP_DECORATOR = pytest.mark.skipif(
    _SKIP_REASON is not None,
    reason=_SKIP_REASON or "",
)


@_SKIP_DECORATOR
class TestVitalsGolden:
    """Accuracy of VitalsReader on labelled golden frames (T08 data)."""

    @staticmethod
    def _cases() -> list[tuple[str, Path, float, float]]:
        """Return (name, image_path, expected_hp, expected_mp) cases."""
        golden_dir = _golden_dir()
        labels_path = _labels_path()
        assert golden_dir is not None  # guaranteed by skipif
        assert labels_path is not None  # guaranteed by skipif

        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        cases: list[tuple[str, Path, float, float]] = []
        for name, entry in labels.items():
            image = golden_dir / name
            hp = entry.get("hp_percent")
            mp = entry.get("mp_percent")
            if image.is_file() and hp is not None and mp is not None:
                cases.append((name, image, float(hp), float(mp)))
        return cases

    def test_frames_loaded(self) -> None:
        """Sanity check: at least one labelled golden frame is loadable."""
        import cv2

        cases = self._cases()
        assert cases, "labels.json exists but no matching golden images found"
        for name, image, _, _ in cases:
            frame = cv2.imread(str(image))
            assert frame is not None, f"could not decode golden frame {name!r}"

    def test_hp_accuracy(self) -> None:
        """HP% within +/-2 of the labelled value for every golden frame."""
        import cv2
        from moonlight.perception.vitals import VitalsReader

        reader = VitalsReader(_usable_profile())
        for name, image, expected_hp, _ in self._cases():
            bgr = cv2.imread(str(image))
            assert bgr is not None, f"could not decode golden frame {name!r}"
            frame = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            got = reader.read_hp(frame)
            assert abs(got - expected_hp) <= ACCURACY_TOLERANCE, (
                f"{name!r}: HP={got:.1f}% but label says {expected_hp:.1f}%"
            )

    def test_mp_accuracy(self) -> None:
        """MP% within +/-2 of the labelled value for every golden frame."""
        import cv2
        from moonlight.perception.vitals import VitalsReader

        reader = VitalsReader(_usable_profile())
        for name, image, _, expected_mp in self._cases():
            bgr = cv2.imread(str(image))
            assert bgr is not None, f"could not decode golden frame {name!r}"
            frame = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            got = reader.read_mp(frame)
            assert abs(got - expected_mp) <= ACCURACY_TOLERANCE, (
                f"{name!r}: MP={got:.1f}% but label says {expected_mp:.1f}%"
            )
