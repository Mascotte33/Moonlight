"""Golden-frame SMOKE tests for the unlabelled perception readers (T28).

SMOKE coverage, not accuracy coverage: labels.json has no ground truth for
sprites, cursor, loot or the server log, and we do not fabricate any. Every
reader runs over every real golden frame (2560x1369) and must

* not raise, and
* return well-formed output (right type/shape, in-frame coordinates,
  confidences in range).

The template readers additionally get a self-match check: their template is
cut from the first frame, so on that frame they must find it at the cut
position (proves the matching path works at real resolution). Battle,
minimap and floor also get a smoke pass here because their accuracy suites
skip for lack of labels/templates; their detection counts are printed in
the "golden accuracy" section of the run (informational, not asserted).

Frames are decoded once (module fixture); each test asserts on the stored
per-frame outputs. Without golden frames the module skips.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from moonlight.setup.calibrate import CalibrationProfile, default_profiles_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIRS = (REPO_ROOT / "data" / "golden", REPO_ROOT / "tests" / "golden")
PROFILE = default_profiles_dir() / "moonlight_user.json"

#: Game viewport of the golden client layout (not in the profile: only the
#: smoke test uses it).
GAME_GRID = (416, 60, 1442, 1056)
#: Server-log text area at the bottom of the golden client layout.
LOG_REGION = (180, 1210, 2020, 135)

#: Template cuts from the first frame: (x, y, w, h).
SPRITE_CUT = (1105, 556, 64, 64)  # player character, viewport center
CURSOR_CUT = (2520, 20, 16, 16)  # minimap compass detail
LOOT_TITLE_CUT = (0, 0, 120, 16)  # "Loot Analyser" title bar
LOOT_ITEM_CUT = (10, 40, 20, 20)  # an item icon under it


def _frames() -> list[Path]:
    for d in GOLDEN_DIRS:
        if d.is_dir():
            found = sorted(p for p in d.iterdir() if p.suffix.lower() == ".png")
            if found:
                return found
    return []


_FRAMES = _frames()
pytestmark = pytest.mark.skipif(
    not _FRAMES or not PROFILE.is_file(),
    reason="no golden frames (tests/golden/*.png) or no moonlight_user.json profile",
)


@dataclass
class Run:
    """Per-reader outputs over all frames (exceptions kept, not raised)."""

    shape: tuple[int, int] = (0, 0)
    outputs: dict[str, list[Any]] = field(default_factory=dict)
    errors: dict[str, list[str]] = field(default_factory=dict)


def _cut(rgb: Any, rect: tuple[int, int, int, int]) -> Any:
    import cv2

    x, y, w, h = rect
    return cv2.cvtColor(rgb[y : y + h, x : x + w], cv2.COLOR_RGB2GRAY).copy()


@pytest.fixture(scope="module")
def run() -> Run:
    """Decode every frame once and run every reader on it."""
    import cv2

    from moonlight.perception import (
        BattleReader,
        CursorReader,
        FloorReader,
        LogOCR,
        LogOCRConfig,
        MinimapReader,
        SpriteDetector,
    )
    from moonlight.perception.loot import TITLE_TEMPLATE, LootReader

    profile = CalibrationProfile.load(PROFILE)
    first_bgr = cv2.imread(str(_FRAMES[0]))
    assert first_bgr is not None
    first = cv2.cvtColor(first_bgr, cv2.COLOR_BGR2RGB)
    log_ocr = LogOCR()
    log_config = LogOCRConfig(log_region=LOG_REGION, enabled=True)
    readers: dict[str, Any] = {
        "sprites": SpriteDetector(
            game_grid_region=GAME_GRID,
            monster_templates={"probe": [_cut(first, SPRITE_CUT)]},
        ).detect,
        "cursor": CursorReader(
            cursor_region=(0, 0, first.shape[1], first.shape[0]),
            cursor_templates=_cut(first, CURSOR_CUT),
        ).read,
        "loot": LootReader(
            {TITLE_TEMPLATE: _cut(first, LOOT_TITLE_CUT), "probe": _cut(first, LOOT_ITEM_CUT)},
            region=(0, 0, 400, 400),
        ).read_loot_window,
        "logocr": lambda f: log_ocr.read(f, log_config),
        "battle": BattleReader(profile).read_battle_list,
        "floor": FloorReader(profile).read_floor,
    }
    minimap = MinimapReader(profile)

    def read_minimap(frame: Any) -> Any:
        minimap.reset()  # frames are independent screenshots, not a video
        return minimap.read_position(frame)

    readers["minimap"] = read_minimap

    result = Run(shape=(first.shape[0], first.shape[1]))
    for name in readers:
        result.outputs[name] = []
        result.errors[name] = []
    for path in _FRAMES:
        bgr = cv2.imread(str(path))
        assert bgr is not None, f"could not decode {path.name!r}"
        frame = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        for name, read in readers.items():
            try:
                result.outputs[name].append(read(frame))
            except Exception as exc:  # noqa: BLE001 - reported by the tests
                result.outputs[name].append(None)
                result.errors[name].append(f"{path.name}: {exc!r}")
    return result


def _in_frame(run: Run, x: int, y: int) -> bool:
    h, w = run.shape
    return 0 <= x < w and 0 <= y < h


def _no_errors(run: Run, name: str) -> None:
    assert not run.errors[name], "\n".join(run.errors[name][:10])
    assert len(run.outputs[name]) == len(_FRAMES)


def test_sprites_smoke(run: Run, golden_summary: list[str]) -> None:
    from moonlight.perception import SpriteDetection

    _no_errors(run, "sprites")
    for hits in run.outputs["sprites"]:
        assert isinstance(hits, list)
        for hit in hits:
            assert isinstance(hit, SpriteDetection)
            assert hit.monster == "probe" and 0.0 < hit.confidence <= 1.0 + 1e-6
            assert _in_frame(run, hit.x, hit.y)
            assert _in_frame(run, hit.x + hit.width - 1, hit.y + hit.height - 1)
    assert (SPRITE_CUT[0], SPRITE_CUT[1]) in {(h.x, h.y) for h in run.outputs["sprites"][0]}
    n = sum(bool(h) for h in run.outputs["sprites"])
    golden_summary.append(f"smoke sprites: ok on {len(_FRAMES)} frames, probe found on {n}")


def test_cursor_smoke(run: Run, golden_summary: list[str]) -> None:
    _no_errors(run, "cursor")
    for pos in run.outputs["cursor"]:
        if pos is not None:
            assert isinstance(pos, tuple) and len(pos) == 2
            assert all(isinstance(v, int) for v in pos)
            assert _in_frame(run, *pos)
    x, y, w, h = CURSOR_CUT
    assert run.outputs["cursor"][0] == (x + w // 2, y + h // 2)
    n = sum(p is not None for p in run.outputs["cursor"])
    golden_summary.append(f"smoke cursor: ok on {len(_FRAMES)} frames, probe found on {n}")


def test_loot_smoke(run: Run, golden_summary: list[str]) -> None:
    from moonlight.perception.loot import LootWindow

    _no_errors(run, "loot")
    for window in run.outputs["loot"]:
        if window is None:
            continue
        assert isinstance(window, LootWindow) and window.open is True
        for index, item, count in window.items:
            assert 0 <= index < 25 and item == "probe" and count >= 1
    assert run.outputs["loot"][0] is not None  # its own title template
    n = sum(w is not None for w in run.outputs["loot"])
    golden_summary.append(f"smoke loot: ok on {len(_FRAMES)} frames, title found on {n}")


def test_logocr_smoke(run: Run, golden_summary: list[str]) -> None:
    from moonlight.perception import LogFrame

    _no_errors(run, "logocr")
    for log in run.outputs["logocr"]:
        assert isinstance(log, LogFrame)
        assert 0.0 <= log.confidence <= 1.0
        assert all(isinstance(line, str) and line for line in log.lines)
        if not log.ok:  # fail closed: nothing half-read leaks out
            assert log.lines == [] and log.events == []
    n = sum(log.ok for log in run.outputs["logocr"])
    golden_summary.append(
        f"smoke logocr: ok on {len(_FRAMES)} frames, OCR succeeded on {n} "
        "(0 = Tesseract binary missing -> fail-closed)"
    )


def test_battle_smoke(run: Run, golden_summary: list[str]) -> None:
    from moonlight.perception import BattleEntry

    _no_errors(run, "battle")
    for entries in run.outputs["battle"]:
        assert all(isinstance(e, BattleEntry) for e in entries)
        assert [e.index for e in entries] == sorted({e.index for e in entries})
        assert sum(e.is_selected for e in entries) <= 1
    with_entries = sum(bool(e) for e in run.outputs["battle"])
    selected = sum(any(x.is_selected for x in e) for e in run.outputs["battle"])
    golden_summary.append(
        f"smoke battle: ok on {len(_FRAMES)} frames, entries on {with_entries}, "
        f"a selected target on {selected} (unlabelled; see test_battle_golden)"
    )


def test_minimap_smoke(run: Run, golden_summary: list[str]) -> None:
    from moonlight.bot.state import Position

    _no_errors(run, "minimap")
    for pos in run.outputs["minimap"]:
        assert pos is None or isinstance(pos, Position)
    n = sum(p is not None for p in run.outputs["minimap"])
    golden_summary.append(
        f"smoke minimap: player marker detected on {n}/{len(_FRAMES)} frames "
        "(misses = map-mark icon over the center)"
    )


def test_floor_smoke(run: Run) -> None:
    _no_errors(run, "floor")
    for floor in run.outputs["floor"]:
        assert floor is None or 0 <= floor <= 15
