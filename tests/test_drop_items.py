"""Drop items: picked pictures leave the backpacks for a free sqm within 2."""

# doc: docs/prs/drop-items.md#drop-items

from __future__ import annotations

import random
from pathlib import Path

import cv2
import numpy as np
import pytest

from moonlight.core.state import Monster, Position, WorldState
from moonlight.looting.drop_items import (
    DropItems,
    drop_picture_path,
    drop_pictures,
    free_drop_slots,
)

GRID = (0, 0, 150, 110)  # 10 px sqm; the backpacks are right of x 150
BOOTS_AT = (200, 40)  # top-left of the boots in the frame


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Input:
    def __init__(self) -> None:
        self.sent: list[tuple[object, ...]] = []

    def mouse_to(self, x: int, y: int) -> None:
        self.sent.append(("to", x, y))

    def mouse_down(self, side: object) -> None:
        self.sent.append(("down",))

    def mouse_up(self, side: object) -> None:
        self.sent.append(("up",))


def boots(tmp_path: Path) -> np.ndarray:
    picture = np.random.default_rng(1).integers(0, 255, (16, 16, 3), dtype=np.uint8)
    cv2.imwrite(str(tmp_path / "leather boots.png"), picture)
    return picture


def frame_with(picture: np.ndarray | None) -> np.ndarray:
    frame = np.zeros((120, 300, 3), np.uint8)
    if picture is not None:
        x, y = BOOTS_AT
        frame[y : y + 16, x : x + 16] = picture
    return frame


def snap(monsters: int = 0) -> WorldState:
    s = WorldState.default()
    s.position = Position(100, 100, 7)
    s.monsters = [Monster("Rat", 100.0, battle_list_row=i) for i in range(monsters)]
    return s


def dropper(tmp_path: Path, frames: list[np.ndarray], clock: Clock, inp: Input) -> DropItems:
    def grab() -> np.ndarray:
        return frames[0] if len(frames) == 1 else frames.pop(0)

    return DropItems(
        inp,  # type: ignore[arg-type]
        lambda point: point,
        grab,
        GRID,
        tmp_path,
        clock=clock,
        rng=random.Random(0),
    )


def run_round(drop: DropItems, clock: Clock, state: WorldState, ticks: int = 40) -> None:
    for _ in range(ticks):
        if not drop.due(state):
            return
        drop.step(state)
        clock.now += 0.05


def test_item_names_become_file_names(tmp_path: Path) -> None:
    assert drop_picture_path(tmp_path, " leather boots ") == tmp_path / "leather boots.png"
    for bad in ("", "  ", "a/b", "..", "x?"):
        with pytest.raises(ValueError, match="not an item name"):
            drop_picture_path(tmp_path, bad)


def test_free_slots_are_within_2_with_mine_and_skip_creatures_and_walls() -> None:
    state = snap()
    state.creature_slots = ((8, 5),)
    slots = free_drop_slots(state, lambda x, y, z: x != 98)  # a wall 2 west
    assert (7, 5) in slots and (8, 5) not in slots  # my own sqm: items drop while walking
    assert all(abs(c - 7) <= 2 and abs(r - 5) <= 2 for c, r in slots)
    assert not any(c == 5 for c, _ in slots)
    assert len(slots) == 25 - 1 - 5


def test_waits_1_to_3_minutes_even_with_monsters_in_view(tmp_path: Path) -> None:
    clock = Clock()
    drop = dropper(tmp_path, [frame_with(None)], clock, Input())
    assert not drop.due(snap())
    clock.now = 180.0
    assert drop.due(snap(monsters=1))  # owner 2026-11-27: no wait for an empty battle list
    assert drop.due(snap())


def test_drags_the_item_to_a_free_sqm_and_ends_when_it_is_gone(tmp_path: Path) -> None:
    picture = boots(tmp_path)
    clock, inp = Clock(), Input()
    # the look, the check before the drag, then gone
    frames = [frame_with(picture), frame_with(picture), frame_with(None)]
    drop = dropper(tmp_path, frames, clock, inp)
    clock.now = 180.0
    run_round(drop, clock, snap())
    assert not drop.in_round
    assert [s[0] for s in inp.sent] == ["to", "down", "to", "up"]
    assert inp.sent[0][1:] == (BOOTS_AT[0] + 8, BOOTS_AT[1] + 8)  # the item's middle
    _, x, y = inp.sent[2]
    assert isinstance(x, int) and isinstance(y, int)
    assert x < 150 and abs(x - 75) <= 20 and abs(y - 55) <= 20  # within 2 sqm of me


def test_an_item_that_stays_gets_3_sqm_then_is_left(tmp_path: Path) -> None:
    picture = boots(tmp_path)
    clock, inp = Clock(), Input()
    drop = dropper(tmp_path, [frame_with(picture)], clock, inp)
    clock.now = 180.0
    run_round(drop, clock, snap(), ticks=200)
    assert not drop.in_round
    targets = [s[1:] for s in inp.sent[2::4]]
    assert len(targets) == 3 and len(set(targets)) == 3


def test_with_every_sqm_around_taken_my_own_is_left() -> None:
    state = snap()
    state.creature_slots = tuple((7 + dx, 5 + dy) for dx in range(-2, 3) for dy in range(-2, 3))
    assert free_drop_slots(state, None) == [(7, 5)]


def test_a_monster_mid_round_does_not_stop_it(tmp_path: Path) -> None:
    picture = boots(tmp_path)
    clock, inp = Clock(), Input()
    frames = [frame_with(picture), frame_with(picture), frame_with(None)]
    drop = dropper(tmp_path, frames, clock, inp)
    clock.now = 180.0
    drop.step(snap())  # the first look starts a drag
    run_round(drop, clock, snap(monsters=2))
    assert [s[0] for s in inp.sent] == ["to", "down", "to", "up"]
    assert not drop.in_round  # ended because the item is gone, not for the monsters


def test_the_drop_list_is_apart_from_the_saved_pictures(tmp_path: Path) -> None:
    from moonlight.looting.drop_items import dropping, saved_pictures, set_dropping

    for name in ("boots", "leather armor", "fish"):
        (tmp_path / f"{name}.png").write_bytes(b"x")
    assert dropping(tmp_path) == ["boots", "fish", "leather armor"]  # no list yet: all
    set_dropping(tmp_path, ["fish", "boots", "fish"])
    assert dropping(tmp_path) == ["fish", "boots"]
    assert [p.stem for p in drop_pictures(tmp_path)] == ["boots", "fish"]
    set_dropping(tmp_path, ["boots"])  # "-": off the list, the picture stays
    assert [p.stem for p in saved_pictures(tmp_path)] == ["boots", "fish", "leather armor"]
    assert [p.stem for p in drop_pictures(tmp_path)] == ["boots"]


def test_only_the_clipped_item_itself_is_found(tmp_path: Path) -> None:
    # 2026-11-21: the grey 0.8 match threw away an item that was not on the list.
    from moonlight.looting.drop_items import find_item

    rng = np.random.default_rng(3)
    item = rng.integers(0, 255, (32, 32, 3), np.uint8)
    cv2.imwrite(str(tmp_path / "torch.png"), item)
    frame = np.full((200, 200, 3), 60, np.uint8)
    lookalike = item.copy()
    lookalike[:, :, 0] = 255 - lookalike[:, :, 0]  # same shape, other colours
    frame[20:52, 20:52] = lookalike
    assert find_item(frame, tmp_path / "torch.png") is None
    counted = item.copy()
    counted[24:, 20:] = 255  # another stack count in the corner
    frame[100:132, 100:132] = counted
    assert find_item(frame, tmp_path / "torch.png") == (116, 116)


def test_an_item_that_moved_before_the_drag_is_not_dragged(tmp_path: Path) -> None:
    # 2026-11-21: a loot press or a used item shifted the backpack between the look
    # and the drag, and an item not on the list was thrown away.
    picture = boots(tmp_path)
    clock, inp = Clock(), Input()
    frames = [frame_with(picture), frame_with(None)]  # the look sees it; then it moved
    drop = dropper(tmp_path, frames, clock, inp)
    clock.now = 180.0
    drop.step(snap())  # the look starts a drag
    drop.step(snap())  # the mouse goes to it
    drop.step(snap())  # the check: gone from there
    for _ in range(3):
        drop.step(snap())
    assert [c for c in inp.sent if c[0] != "to"] == []  # never pressed, never dropped


def test_one_look_drags_every_item_last_slot_first(tmp_path: Path) -> None:
    # owner 2026-11-28: one drop per 2-3 s, then back to back was way too fast.
    picture = boots(tmp_path)
    fish = np.random.default_rng(2).integers(0, 255, (16, 16, 3), dtype=np.uint8)
    cv2.imwrite(str(tmp_path / "fish.png"), fish)
    both = frame_with(picture)
    both[80:96, 230:246] = fish
    clock, inp = Clock(), Input()
    frames = [both, both, both, frame_with(None)]  # look, check, check, gone
    drop = dropper(tmp_path, frames, clock, inp)
    clock.now = 180.0
    for _ in range(5):  # the first drag
        drop.step(snap())
    drop.step(snap())
    assert len(inp.sent) == 4  # a pause before the next drag
    clock.now += 1.4  # no new look: the next queued item
    for _ in range(5):
        drop.step(snap())
    presses = [s for s in inp.sent if s[0] == "down"]
    assert len(presses) == 2
    assert inp.sent[0][1:] == (238, 88)  # the fish: the later slot first
    assert inp.sent[4][1:] == (BOOTS_AT[0] + 8, BOOTS_AT[1] + 8)
    run_round(drop, clock, snap())
    assert not drop.in_round


def test_a_fight_start_starts_a_round_once(tmp_path: Path) -> None:
    # owner 2026-11-28: drop at once when a fight starts.
    from types import SimpleNamespace

    clock = Clock()
    drop = dropper(tmp_path, [frame_with(None)], clock, Input())
    drop.hand = SimpleNamespace(moving=False)  # type: ignore[assignment]
    clock.now = 1.0
    assert not drop.due(snap())  # no fight
    assert drop.due(snap(monsters=1))
    run_round(drop, clock, snap(monsters=1))  # nothing to drop: the round ends
    assert not drop.due(snap(monsters=1))  # once per fight
    assert not drop.due(snap())
    assert drop.due(snap(monsters=1))  # the next fight
    drop.in_round = False
    drop.hand = SimpleNamespace(moving=True)  # type: ignore[assignment]
    assert not drop.due(snap())
