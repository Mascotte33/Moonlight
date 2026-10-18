"""Golden-frame accuracy tests for moonlight.perception.minimap (T21/T08).

These tests validate :class:`moonlight.perception.minimap.MinimapReader`
against human-labelled golden screenshots:

* frames:    ``data/golden/*.png`` (preferred, per T12 spec) or
             ``tests/golden/*.png`` (the default output of
             ``tools/label_golden.py``)
* labels:    ``tests/golden/labels.json`` or ``data/golden/labels.json``
             (stores ``minimap_visible`` per frame; optional
             ``floor_level`` for z-checks)
* regions:   the calibrated ``minimap`` rect from ``profiles/<name>.json``
             (produced by ``tools/calibrate.py``)

What is verifiable before world-mapping (T23):

* the player marker is detected whenever the label says the minimap is
  visible (``read_position`` returns a Position instead of None);
* the z-level matches the labelled floor when floor templates exist.

Absolute x/y accuracy needs a MapMatcher + labelled world coordinates
(T23) and is intentionally not asserted here yet.

IMPORTANT: no golden data exists yet (T08 pending -- the user will provide
screenshots). Until then, every test in this module skips cleanly. We
deliberately do NOT fabricate golden images or labels; the skip logic below
is the only thing that runs without data.
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

#: Where per-level floor icons may live (for the z-check).
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
    """Return the first profile that has a calibrated minimap rect."""
    if not PROFILE_DIR.is_dir():
        return None
    from moonlight.calibrate import CalibrationProfile

    for path in sorted(PROFILE_DIR.glob("*.json")):
        try:
            profile = CalibrationProfile.load(path)
        except Exception:
            continue
        if "minimap" in profile.regions:
            return profile
    return None


def _templates_present() -> bool:
    """True when at least one floor template icon exists on disk."""
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
            "golden frames and labels present but no profile with a minimap "
            "rect -- run tools/calibrate.py first"
        )
    return None


_SKIP_REASON = _skip_reason()

_SKIP_DECORATOR = pytest.mark.skipif(
    _SKIP_REASON is not None,
    reason=_SKIP_REASON or "",
)


@_SKIP_DECORATOR
class TestMinimapGolden:
    """Marker detection of MinimapReader on labelled golden frames (T08)."""

    @staticmethod
    def _cases() -> list[tuple[str, Path, bool, int | None]]:
        """Return (name, image_path, minimap_visible, floor_level) cases."""
        golden_dir = _golden_dir()
        labels_path = _labels_path()
        assert golden_dir is not None  # guaranteed by skipif
        assert labels_path is not None  # guaranteed by skipif

        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        cases: list[tuple[str, Path, bool, int | None]] = []
        for name, entry in labels.items():
            image = golden_dir / name
            visible = entry.get("minimap_visible")
            floor = entry.get("floor_level")
            if image.is_file() and visible is not None:
                floor_value = int(floor) if floor is not None else None
                cases.append((name, image, bool(visible), floor_value))
        return cases

    def test_frames_loaded(self) -> None:
        """Sanity check: at least one labelled golden frame is loadable."""
        import cv2

        cases = self._cases()
        assert cases, "labels.json exists but no minimap-labelled golden images"
        for name, image, _, _ in cases:
            frame = cv2.imread(str(image))
            assert frame is not None, f"could not decode golden frame {name!r}"

    def test_marker_detection(self) -> None:
        """Player marker detected exactly when the label says it is visible."""
        import cv2

        from moonlight.perception.minimap import MinimapReader

        reader = MinimapReader(_usable_profile())
        for name, image, visible, _ in self._cases():
            bgr = cv2.imread(str(image))
            assert bgr is not None, f"could not decode golden frame {name!r}"
            frame = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            pos = reader.read_position(frame)
            if visible:
                assert pos is not None, (
                    f"{name!r}: minimap labelled visible but no player marker "
                    "detected"
                )
            else:
                assert pos is None, (
                    f"{name!r}: minimap labelled not visible but a marker was "
                    "detected"
                )


@_SKIP_DECORATOR
class TestMinimapZGolden:
    """z-level from an injected FloorReader on labelled golden frames (T08).

    Skips additionally when floor templates are not yet provided.
    """

    _Z_SKIP = (
        _SKIP_REASON
        or (
            None
            if _templates_present()
            else "floor templates missing (data/templates/floorLevels/) -- "
            "provide the 0-15 level icons first"
        )
    )

    @pytest.mark.skipif(_Z_SKIP is not None, reason=_Z_SKIP or "")
    def test_z_accuracy(self) -> None:
        """Position.z matches the labelled floor for every golden frame."""
        import cv2

        from moonlight.perception.floor import FloorReader
        from moonlight.perception.minimap import MinimapReader

        profile = _usable_profile()
        floor_reader = FloorReader(profile)
        reader = MinimapReader(profile, floor_reader=floor_reader)
        for name, image, visible, expected_floor in TestMinimapGolden._cases():
            if not visible or expected_floor is None:
                continue
            bgr = cv2.imread(str(image))
            assert bgr is not None, f"could not decode golden frame {name!r}"
            frame = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            pos = reader.read_position(frame)
            assert pos is not None, f"{name!r}: expected a position"
            assert pos.z == expected_floor, (
                f"{name!r}: z={pos.z} but label says floor {expected_floor}"
            )
