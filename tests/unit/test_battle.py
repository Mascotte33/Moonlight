"""Unit tests for moonlight.perception.battle.

All tests are offline and use SYNTHETIC numpy battle-list images -- no live
client and no golden screenshots are required. The golden-image tests live
in ``tests/perf/test_battle_golden.py`` and skip until real frames are
provided (T08).

The synthetic frames mimic the measured layout from docs/ui_layout.md #3:
22 px slots, 156 px content width, 11 px top padding, bright text
(grayscale ~247) on a dark background, and the selected row's 20 px box
corners carrying the attack-highlight grey (76/166).
"""

from __future__ import annotations

import time
from typing import TypedDict

import numpy as np
import pytest
from moonlight.calibrate import CalibrationProfile, Region
from moonlight.perception.battle import (
    BattleEntry,
    BattleReader,
    entries_to_state,
)

BG: tuple[int, int, int] = (40, 40, 40)  # dark battle-list background
TEXT_RGB: tuple[int, int, int] = (247, 247, 247)  # grayscale 247 > 150

SLOT_H = 22
CONTENT_W = 156
TOP_PAD = 11


class RowSpec(TypedDict, total=False):
    """Optional flags for one synthetic battle-list slot."""

    text: bool
    highlight: bool
    highlight_gray: int


class FakeResolver:
    """Deterministic stand-in for a template/OCR name resolver."""

    def __init__(self, names: dict[int, str]) -> None:
        self._names = names

    def resolve(self, row: int, slot_gray: np.ndarray) -> str:
        return self._names.get(row, "")


def slot_origin(offset: tuple[int, int], row: int) -> tuple[int, int]:
    """Top-left frame coordinates of battle-list *row* at *offset*."""
    return offset[1] + TOP_PAD + row * SLOT_H, offset[0]


def make_frame(
    rows: list[RowSpec] | None = None,
    offset: tuple[int, int] = (0, 0),
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Build a synthetic frame containing a battle list at *offset*.

    Each row spec may set:
        text: draw a synthetic word in the name area (x 25..44)
        highlight: paint the 20 px box corners with the highlight grey
        highlight_gray: highlight grey to use (default 76)

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
            grey = int(spec.get("highlight_gray", 76))
            for r, c in ((0, 0), (0, 19), (19, 0), (19, 19)):
                slot[r, c] = (grey, grey, grey)
        if spec.get("text"):
            for c in range(25, 45):
                if c % 2 == 0:
                    slot[7:15, c] = TEXT_RGB
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

    def test_single_entry(self) -> None:
        frame, region = make_frame([{"text": True}])
        reader = BattleReader(name_resolver=FakeResolver({0: "wolf"}))
        entries = reader.read_battle_list(frame, region)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.name == "wolf"
        assert entry.index == 0
        assert entry.is_player is False
        assert entry.is_selected is False
        assert entry.hp_percent is None

    def test_multiple_entries_in_order(self) -> None:
        frame, region = make_frame([{"text": True}] * 3)
        reader = BattleReader(name_resolver=FakeResolver({0: "wolf", 1: "rat", 2: "snake"}))
        entries = reader.read_battle_list(frame, region)
        assert [e.name for e in entries] == ["wolf", "rat", "snake"]
        assert [e.index for e in entries] == [0, 1, 2]

    def test_empty_rows_skip_in_index(self) -> None:
        frame, region = make_frame([{"text": True}, {}, {"text": True}])
        entries = read_default(frame, region)
        assert [e.index for e in entries] == [0, 2]

    def test_no_resolver_gives_empty_names(self) -> None:
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
        frame, region = make_frame([])
        offset = (0, 0)
        paint_pixels(frame, offset, 0, range(25, 30, 2), range(8, 9), TEXT_RGB)
        entries = read_default(frame, region)
        assert len(entries) == 1

    def test_threshold_is_strictly_greater(self) -> None:
        # Pixels at exactly 150 are NOT text (threshold is > 150).
        frame, region = make_frame([])
        offset = (0, 0)
        paint_pixels(frame, offset, 0, range(25, 32, 2), range(8, 9), (150, 150, 150))
        assert read_default(frame, region) == []
        # The same pixels at 151 ARE text.
        frame2, region2 = make_frame([])
        paint_pixels(frame2, offset, 0, range(25, 32, 2), range(8, 9), (151, 151, 151))
        assert len(read_default(frame2, region2)) == 1

    def test_max_slots_cap(self) -> None:
        frame, region = make_frame([{"text": True}] * 25)
        entries = read_default(frame, region)
        assert len(entries) == 20
        assert [e.index for e in entries] == list(range(20))


class TestSelectedTarget:
    """Attack-highlight detection at the slot box corners."""

    def test_selected_detected_gray_76(self) -> None:
        frame, region = make_frame([{"text": True}, {"text": True, "highlight": True}])
        entries = read_default(frame, region)
        assert [e.is_selected for e in entries] == [False, True]

    def test_selected_detected_gray_166(self) -> None:
        spec: RowSpec = {"text": True, "highlight": True, "highlight_gray": 166}
        frame, region = make_frame([spec])
        entries = read_default(frame, region)
        assert entries[0].is_selected is True

    def test_no_selection_without_highlight(self) -> None:
        frame, region = make_frame([{"text": True}] * 2)
        entries = read_default(frame, region)
        assert all(not e.is_selected for e in entries)

    def test_partial_highlight_not_selected(self) -> None:
        # Only two of the four corners highlighted: not the target row.
        frame, region = make_frame([{"text": True}])
        top, left = slot_origin((0, 0), 0)
        frame[top, left] = (76, 76, 76)
        frame[top, left + 19] = (76, 76, 76)
        entries = read_default(frame, region)
        assert entries[0].is_selected is False

    def test_highlight_within_tolerance(self) -> None:
        # 76 + 15 = 91 is within the default tolerance of 30.
        spec: RowSpec = {"text": True, "highlight": True, "highlight_gray": 91}
        frame, region = make_frame([spec])
        entries = read_default(frame, region)
        assert entries[0].is_selected is True

    def test_highlight_outside_tolerance(self) -> None:
        # 120 is >30 away from both 76 and 166: not a highlight.
        spec: RowSpec = {"text": True, "highlight": True, "highlight_gray": 120}
        frame, region = make_frame([spec])
        entries = read_default(frame, region)
        assert entries[0].is_selected is False


class TestPlayerDetection:
    """is_player from resolved name vs known monster names."""

    def _reader(self, names: dict[int, str], monsters: set[str] | None) -> BattleReader:
        return BattleReader(name_resolver=FakeResolver(names), monster_names=monsters)

    def test_unknown_name_is_player(self) -> None:
        frame, region = make_frame([{"text": True}] * 2)
        reader = self._reader({0: "wolf", 1: "Kamil"}, {"wolf", "rat"})
        entries = reader.read_battle_list(frame, region)
        assert [e.is_player for e in entries] == [False, True]

    def test_known_monster_name(self) -> None:
        frame, region = make_frame([{"text": True}])
        reader = self._reader({0: "wolf"}, {"wolf"})
        entries = reader.read_battle_list(frame, region)
        assert entries[0].is_player is False

    def test_no_monster_names_means_no_players(self) -> None:
        frame, region = make_frame([{"text": True}])
        reader = self._reader({0: "Kamil"}, None)
        entries = reader.read_battle_list(frame, region)
        assert entries[0].is_player is False

    def test_empty_name_never_player(self) -> None:
        # No resolver: names are empty, so nothing can be a player.
        frame, region = make_frame([{"text": True}])
        reader = BattleReader(monster_names={"wolf"})
        entries = reader.read_battle_list(frame, region)
        assert entries[0].is_player is False


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
        # Region cut off mid-slot: the visible text still forms an entry.
        frame, region = make_frame([{"text": True}])
        ox, oy, rw, rh = region
        entries = read_default(frame, (ox, oy, rw, TOP_PAD + 10))
        assert len(entries) == 1


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

    def test_hp_percent_preserved(self) -> None:
        entries = [BattleEntry(name="wolf", index=0, hp_percent=42.0)]
        monsters, _, _ = entries_to_state(entries)
        assert monsters[0].hp_percent == 42.0


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
            for c in range(25, 45):
                if c % 2 == 0:
                    frame[top + 7 : top + 15, ox + c] = TEXT_RGB
        region = (ox, oy, CONTENT_W, TOP_PAD + n * SLOT_H)

        reader = BattleReader(name_resolver=FakeResolver({i: "wolf" for i in range(n)}))
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
