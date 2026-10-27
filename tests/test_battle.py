"""Unit tests for moonlight.perception.battle.

All tests are offline and use SYNTHETIC numpy battle-list images -- no live
client and no golden screenshots are required. The golden-image tests live
in ``tests/perf/test_battle_golden.py`` and skip until real frames are
provided (T08).

The synthetic frames mimic the measured layout from docs/ui_layout.md #3:
22 px slots, 156 px content width, 11 px top padding, bright text
(grayscale ~247) on a dark background, and the selected row's creature
icon framed by a pure-red 20x20 outline at slot x 1..20, y 0..19 (as
measured on the T08 golden frames, T28).
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import TypedDict

import cv2
import numpy as np
import pytest

from moonlight.calibration.calibrate import CalibrationProfile, Region
from moonlight.perception.battle import (
    BattleEntry,
    BattleReader,
    entries_to_state,
    name_crop_hash,
)

BG: tuple[int, int, int] = (40, 40, 40)  # dark battle-list background
STONE: tuple[int, int, int] = (72, 72, 72)  # real client panel grey (T28)
TEXT_RGB: tuple[int, int, int] = (247, 247, 247)  # grayscale 247 > 150

SLOT_H = 22
CONTENT_W = 156
TOP_PAD = 11


class RowSpec(TypedDict, total=False):
    """Optional flags for one synthetic battle-list slot."""

    text: bool
    bar: bool
    highlight: bool
    highlight_rgb: tuple[int, int, int]
    hp: float
    hp_rgb: tuple[int, int, int]


def slot_origin(offset: tuple[int, int], row: int) -> tuple[int, int]:
    """Top-left frame coordinates of battle-list *row* at *offset*."""
    return offset[1] + TOP_PAD + row * SLOT_H, offset[0]


def make_frame(
    rows: list[RowSpec] | None = None,
    offset: tuple[int, int] = (0, 0),
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Build a synthetic frame containing a battle list at *offset*.

    Each row spec may set:
        text: draw a synthetic word in the name area (x 24..43) and the
            black HP-bar outline under it (row 16, x 24..153)
        bar: HP bar on/off (defaults to *text*; False = another panel's
            text, True without text = a bar-only row)
        highlight: paint the 20x20 target outline around the icon
        highlight_rgb: outline color to use (default pure red)
        hp: HP fraction 0..1 painted as fill under the outline (rows 17..19)
        hp_rgb: fill color (default Tibia green)

    Returns ``(frame, region)`` where *region* covers exactly the widget.
    """
    rows = rows or []
    ox, oy = offset
    n = max(len(rows), 1)
    height = oy + TOP_PAD + n * SLOT_H + 4
    width = ox + CONTENT_W + 4
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (5, 5, 5)  # outside the widget
    frame[oy : oy + TOP_PAD + n * SLOT_H, ox : ox + CONTENT_W] = BG
    for i, spec in enumerate(rows):
        top = oy + TOP_PAD + i * SLOT_H
        slot = frame[top : top + SLOT_H, ox : ox + CONTENT_W]
        if spec.get("highlight"):
            rgb = spec.get("highlight_rgb", (255, 0, 0))
            slot[0, 1:21] = rgb
            slot[19, 1:21] = rgb
            slot[0:20, 1] = rgb
            slot[0:20, 20] = rgb
        if spec.get("text"):
            for c in range(24, 44):
                if c % 2 == 0:
                    slot[7:15, c] = TEXT_RGB
        if spec.get("bar", spec.get("text")):
            slot[16, 24:154] = (0, 0, 0)
        if "hp" in spec:
            # Interior of the outline: x 25..152 (128 px), as Tibia draws it.
            slot[17:20, 25 : 25 + round(spec["hp"] * 128)] = spec.get("hp_rgb", (0, 192, 0))
    region = (ox, oy, CONTENT_W, TOP_PAD + n * SLOT_H)
    return frame, region


def paint_pixels(
    frame: np.ndarray,
    offset: tuple[int, int],
    row: int,
    cols: range,
    text_rows: range,
    rgb: tuple[int, int, int],
) -> None:
    """Paint single pixels inside the name area of one slot."""
    top, left = slot_origin(offset, row)
    for r in text_rows:
        for c in cols:
            frame[top + r, left + c] = rgb


def read_default(frame: np.ndarray, region: tuple[int, int, int, int]) -> list[BattleEntry]:
    """Read with a default reader (no resolver, no monster names)."""
    return BattleReader().read_battle_list(frame, region)


class TestEntryDetection:
    """Slot detection via grayscale text thresholding."""

    def test_empty_list(self) -> None:
        frame, region = make_frame([])
        assert read_default(frame, region) == []

    def test_text_without_hp_bar_is_not_an_entry(self) -> None:
        # Regression (T28): another widget's text at the calibrated rect
        # (no HP bar under it) was read as phantom creatures.
        frame, region = make_frame([{"text": True, "bar": False}, {"text": True}])
        entries = read_default(frame, region)
        assert [e.index for e in entries] == [1]

    @pytest.mark.parametrize("text_columns", [range(10, 45), range(60, 80, 2)])
    def test_text_not_starting_at_the_name_is_another_window(self, text_columns: range) -> None:
        # Live: a dialog's "Name" label crossing the icon gap, and the taskbar.
        frame, region = make_frame([{"bar": True}])
        paint_pixels(frame, (0, 0), 0, text_columns, range(7, 15), TEXT_RGB)
        assert read_default(frame, region) == []

    def test_short_dark_run_is_not_an_hp_bar(self) -> None:
        frame, region = make_frame([{"text": True, "bar": False}])
        top, left = slot_origin((0, 0), 0)
        frame[top + 16, left + 24 : left + 24 + 40] = (0, 0, 0)
        assert read_default(frame, region) == []

    def test_single_entry(self) -> None:
        frame, region = make_frame([{"text": True}])
        entries = read_default(frame, region)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.name == ""
        assert entry.index == 0
        assert entry.is_player is False
        assert entry.is_selected is False
        assert entry.hp_percent is None

    def test_multiple_entries_in_order(self) -> None:
        frame, region = make_frame([{"text": True}] * 3)
        entries = read_default(frame, region)
        assert [e.index for e in entries] == [0, 1, 2]

    def test_empty_rows_skip_in_index(self) -> None:
        frame, region = make_frame([{"text": True}, {}, {"text": True}])
        entries = read_default(frame, region)
        assert [e.index for e in entries] == [0, 2]

    def test_names_stay_empty(self) -> None:
        frame, region = make_frame([{"text": True}] * 2)
        entries = read_default(frame, region)
        assert len(entries) == 2
        assert all(e.name == "" for e in entries)

    def test_below_min_text_pixels_rejected(self) -> None:
        # Only 2 text pixels: below the default minimum of 3.
        frame, region = make_frame([])
        offset = (0, 0)
        paint_pixels(frame, offset, 0, range(25, 29, 2), range(8, 9), TEXT_RGB)
        assert read_default(frame, region) == []

    def test_at_min_text_pixels_accepted(self) -> None:
        # Exactly 3 text pixels: meets the default minimum.
        frame, region = make_frame([{"bar": True}])
        offset = (0, 0)
        paint_pixels(frame, offset, 0, range(25, 30, 2), range(8, 9), TEXT_RGB)
        entries = read_default(frame, region)
        assert len(entries) == 1

    def test_threshold_is_strictly_greater(self) -> None:
        # Pixels at exactly 150 are NOT text (threshold is > 150).
        frame, region = make_frame([{"bar": True}])
        offset = (0, 0)
        paint_pixels(frame, offset, 0, range(25, 32, 2), range(8, 9), (150, 150, 150))
        assert read_default(frame, region) == []
        # The same pixels at 151 ARE text.
        frame2, region2 = make_frame([{"bar": True}])
        paint_pixels(frame2, offset, 0, range(25, 32, 2), range(8, 9), (151, 151, 151))
        assert len(read_default(frame2, region2)) == 1

    def test_max_slots_cap(self) -> None:
        frame, region = make_frame([{"text": True}] * 25)
        entries = read_default(frame, region)
        assert len(entries) == 20
        assert [e.index for e in entries] == list(range(20))


class TestSelectedTarget:
    """Red target-outline detection around the slot's creature icon."""

    def test_selected_detected(self) -> None:
        frame, region = make_frame([{"text": True}, {"text": True, "highlight": True}])
        entries = read_default(frame, region)
        assert [e.is_selected for e in entries] == [False, True]

    def test_stone_background_not_selected(self) -> None:
        # Regression (T28): the old grey-corner rule (76 +/- 30) matched the
        # client's stone panel grey (~64-75) and flagged every row selected.
        frame, region = make_frame([{"text": True}] * 2)
        widget = frame[: region[3], : region[2]]  # offset (0, 0)
        widget[(widget == BG).all(axis=2)] = STONE
        entries = read_default(frame, region)
        assert len(entries) == 2
        assert all(not e.is_selected for e in entries)

    def test_red_hp_bar_not_selected(self) -> None:
        # A low-HP red bar under the name (rows 16-18, x >= 24) is not the
        # target outline.
        frame, region = make_frame([{"text": True}])
        top, left = slot_origin((0, 0), 0)
        frame[top + 16 : top + 19, left + 24 : left + 60] = (255, 0, 0)
        entries = read_default(frame, region)
        assert entries[0].is_selected is False

    def test_no_selection_without_highlight(self) -> None:
        frame, region = make_frame([{"text": True}] * 2)
        entries = read_default(frame, region)
        assert all(not e.is_selected for e in entries)

    def test_partial_highlight_not_selected(self) -> None:
        # Only the top edge is red (e.g. a red icon touching the box): no.
        frame, region = make_frame([{"text": True}])
        top, left = slot_origin((0, 0), 0)
        frame[top, left + 1 : left + 21] = (255, 0, 0)
        entries = read_default(frame, region)
        assert entries[0].is_selected is False

    def test_highlight_within_tolerance(self) -> None:
        # (235, 20, 15) is within the default per-channel tolerance of 30.
        spec: RowSpec = {"text": True, "highlight": True, "highlight_rgb": (235, 20, 15)}
        frame, region = make_frame([spec])
        entries = read_default(frame, region)
        assert entries[0].is_selected is True

    @pytest.mark.parametrize("shift", [-5, -3, -1, 2])
    def test_selected_when_region_is_a_few_px_off(self, shift: int) -> None:
        # Live regression (W1.F5.K1): the panel moves a few px when the
        # backpack above it resizes. The bar (slot rows 14..21) still reads,
        # so the selection must too, or the cavebot re-presses attack.
        rows: list[RowSpec] = [{"text": True}, {"text": True, "highlight": True}, {"text": True}]
        frame, (x, y, w, h) = make_frame(rows, offset=(0, 5))
        entries = read_default(frame, (x, y + shift, w, h))
        assert [e.is_selected for e in entries] == [False, True, False]

    def test_hovered_selected_outline_detected(self) -> None:
        # Live (T1.F12.K1): with the cursor on the targeted row the outline
        # is (255, 128, 128); reading it as unselected re-clicked the row,
        # which untargets it.
        spec: RowSpec = {"text": True, "highlight": True, "highlight_rgb": (255, 128, 128)}
        frame, region = make_frame([spec])
        assert read_default(frame, region)[0].is_selected is True

    def test_white_hover_outline_not_selected(self) -> None:
        spec: RowSpec = {"text": True, "highlight": True, "highlight_rgb": (255, 255, 255)}
        frame, region = make_frame([spec])
        assert read_default(frame, region)[0].is_selected is False

    def test_highlight_outside_tolerance(self) -> None:
        # A green (follow) outline is not the attack target.
        spec: RowSpec = {"text": True, "highlight": True, "highlight_rgb": (0, 255, 0)}
        frame, region = make_frame([spec])
        entries = read_default(frame, region)
        assert entries[0].is_selected is False


class TestPlayerDetection:
    """is_player from the name vs known monster names."""

    def test_unknown_name_is_player(self) -> None:
        reader = BattleReader(monster_names={"wolf", "rat"})
        assert reader._is_player("Kamil") is True
        assert reader._is_player("wolf") is False

    def test_no_monster_names_means_no_players(self) -> None:
        assert BattleReader()._is_player("Kamil") is False

    def test_empty_name_never_player(self) -> None:
        frame, region = make_frame([{"text": True}])
        reader = BattleReader(monster_names={"wolf"})
        assert reader.read_battle_list(frame, region)[0].is_player is False


class TestRegionHandling:
    """Region clipping and placement (window can move/resize)."""

    def test_region_at_offset(self) -> None:
        offset = (37, 129)
        rows: list[RowSpec] = [{"text": True, "highlight": True}]
        frame, region = make_frame(rows, offset=offset)
        entries = read_default(frame, region)
        assert len(entries) == 1
        assert entries[0].is_selected is True

    def test_region_outside_frame(self) -> None:
        frame, _ = make_frame([{"text": True}])
        h, w = frame.shape[:2]
        entries = read_default(frame, (w + 5, h + 5, CONTENT_W, TOP_PAD + SLOT_H))
        assert entries == []

    def test_zero_size_region(self) -> None:
        frame, _ = make_frame([{"text": True}])
        assert read_default(frame, (0, 0, 0, 100)) == []
        assert read_default(frame, (0, 0, CONTENT_W, 0)) == []

    def test_region_clipped_to_frame(self) -> None:
        frame, region = make_frame([{"text": True}])
        ox, oy, rw, rh = region
        big = (0, 0, rw + 100, rh + 100)
        entries = read_default(frame, big)
        assert len(entries) == 1

    def test_partial_last_row_readable(self) -> None:
        # Region cut off mid-slot below the HP bar: still an entry.
        frame, region = make_frame([{"text": True}])
        ox, oy, rw, rh = region
        entries = read_default(frame, (ox, oy, rw, TOP_PAD + 18))
        assert len(entries) == 1

    def test_row_cut_above_hp_bar_not_an_entry(self) -> None:
        # Fail closed: without its HP bar a row cannot be told apart from
        # another panel's text (T28), so a row cut above the bar is dropped.
        frame, region = make_frame([{"text": True}])
        ox, oy, rw, rh = region
        assert read_default(frame, (ox, oy, rw, TOP_PAD + 10)) == []


class TestReaderApi:
    """BattleReader constructor, profile support, and defaults."""

    def test_default_region_from_profile(self) -> None:
        frame, region = make_frame([{"text": True}])
        ox, oy, rw, rh = region
        profile = CalibrationProfile(landmark=(0, 0))
        profile.set_region("battle_list", Region(ox, oy, rw, rh))
        reader = BattleReader(profile)
        entries = reader.read_battle_list(frame)  # no explicit region
        assert len(entries) == 1

    def test_explicit_region_overrides_profile(self) -> None:
        profile = CalibrationProfile(landmark=(0, 0))
        profile.set_region("battle_list", Region(0, 0, CONTENT_W, TOP_PAD + SLOT_H))
        reader = BattleReader(profile, battle_region=(1, 2, 3, 4))
        assert reader._battle_region == (1, 2, 3, 4)

    def test_no_region_returns_empty(self) -> None:
        frame, _ = make_frame([{"text": True}])
        assert BattleReader().read_battle_list(frame) == []

    def test_invalid_region_raises(self) -> None:
        with pytest.raises(ValueError):
            BattleReader(battle_region=(1, 2, 3))  # type: ignore[arg-type]

    def test_hp_percent_defaults_to_none(self) -> None:
        frame, region = make_frame([{"text": True}])
        entries = read_default(frame, region)
        assert entries[0].hp_percent is None

    @pytest.mark.parametrize(
        ("hp", "rgb"),
        [(1.0, (0, 192, 0)), (0.5, (192, 192, 0)), (0.125, (192, 0, 0))],
    )
    def test_hp_percent_read_from_bar_fill(self, hp: float, rgb: tuple[int, int, int]) -> None:
        """F-16: the coloured share of the fill row is the HP percent."""
        frame, region = make_frame([{"text": True, "hp": hp, "hp_rgb": rgb}])
        entries = read_default(frame, region)
        assert entries[0].hp_percent == pytest.approx(hp * 100, abs=1.0)

    def test_hp_percent_per_row(self) -> None:
        frame, region = make_frame([{"text": True, "hp": 0.25}, {"text": True, "hp": 0.75}])
        entries = read_default(frame, region)
        assert [round(e.hp_percent or 0) for e in entries] == [25, 75]


class TestEntriesToState:
    """Mapping battle-list entries onto WorldState combat fields."""

    def test_monsters_and_selected(self) -> None:
        entries = [
            BattleEntry(name="wolf", index=0, is_selected=True),
            BattleEntry(name="rat", index=1),
        ]
        monsters, selected, players = entries_to_state(entries)
        assert [m.name for m in monsters] == ["wolf", "rat"]
        assert selected is not None
        assert selected.name == "wolf"
        assert selected.is_player is False
        assert players == 0

    def test_selected_player(self) -> None:
        entries = [
            BattleEntry(name="wolf", index=0),
            BattleEntry(name="Kamil", index=1, is_player=True, is_selected=True),
        ]
        monsters, selected, players = entries_to_state(entries)
        assert [m.name for m in monsters] == ["wolf"]
        assert selected is not None
        assert selected.name == "Kamil"
        assert selected.is_player is True
        assert players == 1

    def test_players_counted(self) -> None:
        entries = [
            BattleEntry(name="wolf", index=0),
            BattleEntry(name="Kamil", index=1, is_player=True),
            BattleEntry(name="Rex", index=2, is_player=True),
        ]
        monsters, selected, players = entries_to_state(entries)
        assert len(monsters) == 1
        assert selected is None
        assert players == 2

    def test_empty(self) -> None:
        monsters, selected, players = entries_to_state([])
        assert monsters == []
        assert selected is None
        assert players == 0

    def test_hp_percent_defaults_to_100(self) -> None:
        entries = [BattleEntry(name="wolf", index=0)]
        monsters, _, _ = entries_to_state(entries)
        assert monsters[0].hp_percent == 100.0

    def test_hash_and_row_copied(self) -> None:
        entries = [BattleEntry(name="", index=3, name_hash="ab12"), BattleEntry(name="", index=5)]
        monsters, _, _ = entries_to_state(entries)
        assert [(m.name_hash, m.battle_list_row) for m in monsters] == [("ab12", 3), ("", 5)]

    def test_hp_percent_preserved(self) -> None:
        entries = [BattleEntry(name="wolf", index=0, hp_percent=42.0)]
        monsters, _, _ = entries_to_state(entries)
        assert monsters[0].hp_percent == 42.0


def hashes(spec_text: list[tuple[int, int]], **row: object) -> str:
    """Hash of the single entry read from a row whose text is *spec_text* (x, y) pixels."""
    spec: RowSpec = {"bar": True, **row}  # type: ignore[typeddict-item]
    frame, region = make_frame([spec])
    top, left = slot_origin((0, 0), 0)
    for x, y in spec_text:
        frame[top + y, left + x] = TEXT_RGB
    return read_default(frame, region)[0].name_hash


WOLF = [(x, y) for x in range(25, 45, 2) for y in range(7, 15)]
RAT = [(x, y) for x in range(25, 35) for y in range(7, 15, 2)]


class TestNameHash:
    """Stable name identity without OCR."""

    def test_same_text_same_hash_with_different_hp_bar(self) -> None:
        assert hashes(WOLF, hp=1.0) == hashes(WOLF, hp=0.3, hp_rgb=(192, 0, 0))

    def test_same_text_same_hash_with_selection_outline(self) -> None:
        assert hashes(WOLF) == hashes(WOLF, highlight=True)

    @pytest.mark.parametrize("delta", [-1, 1])
    def test_background_brightness_does_not_matter(self, delta: int) -> None:
        crop = np.full((10, 40, 3), 40, dtype=np.uint8)
        crop[2:8, 5:30:2] = 247
        shifted = np.clip(crop.astype(int) + delta, 0, 255).astype(np.uint8)
        shifted[crop == 247] = 247
        assert name_crop_hash(crop) == name_crop_hash(shifted) != ""

    def test_text_position_does_not_matter(self) -> None:
        crop = np.zeros((10, 40, 3), dtype=np.uint8)
        crop[2:8, 5:30:2] = 247
        moved = np.roll(crop, (1, 3), axis=(0, 1))
        assert name_crop_hash(crop) == name_crop_hash(moved)

    def test_different_text_different_hash(self) -> None:
        assert hashes(WOLF) != hashes(RAT)

    def test_coloured_pixels_in_the_name_area_do_not_change_the_hash(self) -> None:
        crop = np.zeros((10, 40, 3), dtype=np.uint8)
        crop[2:8, 5:30:2] = 247
        with_icon = crop.copy()
        with_icon[1:9, 32:38] = (230, 60, 60)
        assert name_crop_hash(crop) == name_crop_hash(with_icon)

    def test_a_row_of_only_coloured_bright_pixels_is_not_an_entry(self) -> None:
        frame, region = make_frame([{"bar": True}])
        top, left = slot_origin((0, 0), 0)
        for x, y in WOLF:
            frame[top + y, left + x] = (230, 60, 60)
        assert read_default(frame, region) == []

    def test_empty_name_area_is_empty_hash(self) -> None:
        assert name_crop_hash(np.full((10, 40, 3), 40, dtype=np.uint8)) == ""


class TestPerformance:
    """Per-read latency on a 1080p-derived region crop (budget < 15 ms)."""

    def test_read_under_15_ms(self) -> None:
        h, w = 1080, 1920
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:] = (5, 5, 5)
        ox, oy, n = 1600, 500, 20
        frame[oy : oy + TOP_PAD + n * SLOT_H, ox : ox + CONTENT_W] = BG
        for i in range(n):
            top = oy + TOP_PAD + i * SLOT_H
            for c in range(24, 44):
                if c % 2 == 0:
                    frame[top + 7 : top + 15, ox + c] = TEXT_RGB
            frame[top + 16, ox + 24 : ox + 154] = (0, 0, 0)
        region = (ox, oy, CONTENT_W, TOP_PAD + n * SLOT_H)

        reader = BattleReader()
        # Warm up, then time several reads.
        for _ in range(3):
            reader.read_battle_list(frame, region)
        start = time.perf_counter()
        iterations = 50
        for _ in range(iterations):
            reader.read_battle_list(frame, region)
        elapsed_ms = (time.perf_counter() - start) * 1000.0 / iterations
        assert elapsed_ms < 15.0, f"read_battle_list took {elapsed_ms:.3f} ms (budget 15 ms)"
        assert len(reader.read_battle_list(frame, region)) == 20


BATTLE_SHOT = Path(__file__).resolve().parents[0] / "golden" / "battle" / "full.png"


@pytest.mark.skipif(not BATTLE_SHOT.exists(), reason="battle list screenshot not present")
def test_ui_scale_reads_an_enlarged_battle_list() -> None:
    shot = cv2.imread(str(BATTLE_SHOT))
    assert shot is not None
    crop = cv2.cvtColor(shot[22:506, 2212:2368], cv2.COLOR_BGR2RGB)
    at_1 = BattleReader(battle_region=(0, 0, 156, 484)).read_battle_list(crop)
    assert len(at_1) == 12 and [e.index for e in at_1 if e.is_selected] == [4]

    def at_1_5(interpolation: int) -> list[BattleEntry]:
        big = cv2.resize(crop, None, fx=1.5, fy=1.5, interpolation=interpolation)
        reader = BattleReader(CalibrationProfile(ui_scale=1.5), battle_region=(0, 0, 234, 726))
        return reader.read_battle_list(big)

    # The name hash depends on the pixel scale, everything else must match.
    assert [replace(e, name_hash="") for e in at_1_5(cv2.INTER_NEAREST)] == [
        replace(e, name_hash="") for e in at_1
    ]
    # Smooth enlarging blurs the 1 px red target frame out of tolerance.
    smooth = at_1_5(cv2.INTER_LINEAR)
    assert [(e.index, e.hp_percent) for e in smooth] == [(e.index, e.hp_percent) for e in at_1]


def test_names_come_from_the_seen_monsters_store() -> None:
    class Store:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, int]] = []

        def name_for(self, name_hash: str, name_crop: np.ndarray, clue_crop: np.ndarray) -> str:
            self.calls.append((name_hash, name_crop.shape[1], clue_crop.shape[1]))
            return "wolf"

    frame, region = make_frame([{"text": True}])
    store = Store()
    entries = BattleReader(seen_monsters=store).read_battle_list(frame, region)  # type: ignore[arg-type]
    assert [e.name for e in entries] == ["wolf"]
    assert [call[0] for call in store.calls] == [entries[0].name_hash] != [""]
    # The clue holds the creature icon left of the name, so it is wider.
    assert store.calls[0][2] > store.calls[0][1]
