"""Golden-frame accuracy tests for moonlight.perception.minimap (T21/T08).

These tests validate :class:`moonlight.perception.minimap.MinimapReader`
against human-labelled golden screenshots:

* frames:    ``data/golden/*.png`` (preferred, per T12 spec) or
             ``tests/golden/*.png`` (the default output of
             ``tools/label_golden.py``)
* labels:    ``tests/golden/labels.json`` or ``data/golden/labels.json``
             (``position_x`` / ``position_y`` / ``position_z`` per frame;
             null = the labeller could not place the player -> skipped)
* regions:   the calibrated ``minimap`` rect from ``moonlight/profiles/<name>.json``
             (produced by ``tools/calibrate.py``)

What is verifiable before world-mapping (T23):

* the player marker is detected on every frame with a labelled position
  (``read_position`` returns a Position instead of None);
* the z-level matches the labelled ``position_z`` when floor templates
  exist.

Absolute x/y accuracy needs a MapMatcher + labelled world coordinates
(T23) and is intentionally not asserted here yet.

Status (T28): the 200 frames load, but every frame's position is null
(``tools/calibrate_auto.py`` does not measure position), so the accuracy
tests skip with that reason. The marker detection rate over all frames is
reported by ``test_golden_smoke.py`` instead. We never fabricate labels.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonlight.bot.state import Position
from moonlight.perception.floor import FloorReader
from moonlight.perception.minimap import MinimapReader

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
PROFILE_DIR = Path(__file__).resolve().parents[1] / "golden_profile"

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
    from moonlight.setup.calibrate import CalibrationProfile

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


Case = tuple[str, Path, int | None, int | None, int | None]


def _cases() -> list[Case]:
    """Return (name, image_path, x, y, z) for every labelled golden frame."""
    golden_dir = _golden_dir()
    labels_path = _labels_path()
    assert golden_dir is not None  # guaranteed by skipif
    assert labels_path is not None  # guaranteed by skipif

    frames = json.loads(labels_path.read_text(encoding="utf-8"))["frames"]
    cases: list[Case] = []
    for name, entry in frames.items():
        image = golden_dir / name
        if image.is_file():
            cases.append(
                (name, image, entry.get("position_x"), entry.get("position_y"),
                 entry.get("position_z"))
            )
    return cases


def _read(reader: MinimapReader, image: Path) -> Position | None:
    """Run ``reader.read_position`` on one golden frame (RGB)."""
    import cv2

    bgr = cv2.imread(str(image))
    assert bgr is not None, f"could not decode golden frame {image.name!r}"
    return reader.read_position(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


@_SKIP_DECORATOR
class TestMinimapGolden:
    """Marker detection of MinimapReader on labelled golden frames (T08)."""

    def test_frames_loaded(self) -> None:
        """Sanity check: every labelled golden frame is loadable."""
        import cv2

        cases = _cases()
        assert cases, "labels.json exists but no matching golden images found"
        for name, image, *_ in cases:
            assert cv2.imread(str(image)) is not None, f"could not decode {name!r}"

    def test_marker_detection(self) -> None:
        """Player marker detected on every frame with a labelled position."""
        placed = [c for c in _cases() if c[2] is not None and c[3] is not None]
        if not placed:
            pytest.skip("no frame has a labelled position_x/position_y")
        reader = MinimapReader(_usable_profile())
        for name, image, *_ in placed:
            reader.reset()
            assert _read(reader, image) is not None, (
                f"{name!r}: position labelled but no player marker detected"
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
        """Position.z matches the labelled position_z for every golden frame."""
        cases = [c for c in _cases() if c[4] is not None]
        if not cases:
            pytest.skip("no frame has a labelled position_z")
        profile = _usable_profile()
        reader = MinimapReader(profile, floor_reader=FloorReader(profile))
        for name, image, _, _, expected_z in cases:
            pos = _read(reader, image)
            assert pos is not None, f"{name!r}: expected a position"
            assert pos.z == expected_z, f"{name!r}: z={pos.z} but label says {expected_z}"
