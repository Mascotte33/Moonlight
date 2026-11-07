"""Unit tests for moonlight.looting.looting: the loot hotkey for rings around the player."""

from __future__ import annotations

import numpy as np

import moonlight.looting.looting as looting_module
from moonlight.core.state import Position, WorldState
from moonlight.hardware.input import FakeInput
from moonlight.looting.looting import (
    RING_CONFIRM_SECONDS,
    RING_GONE_SECONDS,
    RING_GONE_TIMEOUT,
    RING_STEP_SECONDS,
    Looting,
    LootingConfig,
)
from tests.loot_ring_pictures import paint_ring


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


GRID = (0, 0, 1440, 1056)  # 96 px tiles; the player's tile is x 672-767, y 480-575
EAST = (816, 528)  # the middle of the tile east of the player
NEAR = range(0, 2)  # the player's tile and the 8 around it
WS = WorldState(client_focused=True, screen_ok=True, hp_percent=100.0)


class RingFrames:
    """A ring turning on the tile at *x*, until ``gone`` (``flash``: one look, then gone)."""

    def __init__(self, flash: bool, x: int) -> None:
        tile_x = x // 96 * 96
        self.frames = [
            paint_ring(np.zeros((1056, 1440, 3), np.uint8), tile_x, 480, p) for p in (0, 1)
        ]
        self.flash, self.gone, self.tick = flash, False, 0

    def __call__(self) -> np.ndarray:
        self.tick += 1
        if self.gone or (self.flash and self.tick > 1):
            return np.zeros_like(self.frames[0])
        return self.frames[self.tick % 2]


def make_ring(
    clock: Clock, flash: bool = False, x: int = 800, inp: FakeInput | None = None,
    **cfg: object,
) -> Looting:
    config = LootingConfig(**{"wait_ms": 400, **cfg})  # type: ignore[arg-type]
    return Looting(
        inp or FakeInput(), config, clock=clock, frames=RingFrames(flash, x), game_region=GRID
    )


def hide_ring(looting: Looting) -> None:
    looting._frames.gone = True  # type: ignore[union-attr]


def see_ring(looting: Looting, clock: Clock, snap: WorldState = WS) -> None:
    """Looks every 0.125 s until the ring is due; a ring counts only after turning a while."""
    for _ in range(10):
        if looting.ring_due(NEAR, snap):
            return
        clock.t += 0.125
    raise AssertionError("the ring was never due")


def wait(looting: Looting, clock: Clock, seconds: float, snap: WorldState = WS) -> None:
    """Ticks every 0.125 s for *seconds*: the bot holds still all along."""
    end = clock.t + seconds
    while clock.t < end:
        assert not looting.ring_due(NEAR, snap) and looting.loot_first(snap)
        clock.t += 0.125


def test_ring_presses_the_hotkey_then_holds_until_the_rings_are_gone() -> None:
    inp, clock = FakeInput(), Clock()
    looting = make_ring(clock, inp=inp)
    see_ring(looting, clock)
    assert clock.t >= RING_CONFIRM_SECONDS  # it turned a while first
    looting.press_ring()
    assert inp.sent_lines == ["K alt+q"]
    wait(looting, clock, 0.5)  # wait_ms, and the ring still turns
    hide_ring(looting)  # looted: the ring goes away
    wait(looting, clock, RING_GONE_SECONDS - 0.2)
    clock.t += 0.3
    assert not looting.ring_due(NEAR, WS)
    assert not looting.loot_first(WS) and looting.step(WS) is False
    assert inp.sent_lines == ["K alt+q"]
    shots = sorted(p.name for p in looting_module.LOOT_SHOTS_DIR.glob("*.png"))
    assert shots[-1].endswith("_ring.png")


def test_a_flash_seen_once_is_not_looted() -> None:
    inp, clock = FakeInput(), Clock()
    looting = make_ring(clock, flash=True, inp=inp)  # shows for one look, then gone
    for _ in range(12):
        assert not looting.ring_due(NEAR, WS)
        clock.t += 0.125
    assert inp.sent_lines == []


def test_a_creature_on_the_ring_tile_is_not_looted() -> None:
    clock = Clock()
    looting = make_ring(clock)
    amazon_east = WorldState(
        client_focused=True, screen_ok=True, hp_percent=100.0, creature_slots=((8, 5),)
    )
    for _ in range(8):
        assert not looting.ring_due(NEAR, amazon_east)  # its outfit sparkles like a ring
        clock.t += 0.125
    assert looting.ring_due(NEAR, WS)  # it walked off: the corpse's ring is looted


def test_rings_that_stay_get_one_more_press_then_are_left_until_the_character_moves() -> None:
    inp, clock = FakeInput(), Clock()
    looting = make_ring(clock, inp=inp)
    see_ring(looting, clock)
    looting.press_ring()
    wait(looting, clock, RING_GONE_TIMEOUT)
    see_ring(looting, clock)  # still there: pressed again
    looting.press_ring()
    wait(looting, clock, RING_GONE_TIMEOUT)
    for _ in range(8):  # a full backpack: left alone here
        assert not looting.ring_due(NEAR, WS) and not looting.should_loot(WS)
        clock.t += 0.125
    assert inp.sent_lines == ["K alt+q", "K alt+q"]
    moved = WorldState(client_focused=True, screen_ok=True, hp_percent=100.0,
                       position=Position(1, 0, 7))
    assert looting.ring_due(NEAR, moved)  # walked on: rings here are new


def test_no_ring_does_nothing() -> None:
    inp, clock = FakeInput(), Clock()
    looting = Looting(inp, LootingConfig(), clock=clock)
    assert not looting.ring_due(NEAR, WS)
    assert not looting.should_loot(WS) and looting.step(WS) is False
    assert inp.sent_lines == []


def test_low_hp_holds_still_and_loots_once_hp_is_back() -> None:
    clock = Clock()
    looting = make_ring(clock, min_hp_to_loot=70.0)
    low = WorldState(client_focused=True, screen_ok=True, hp_percent=50.0)
    for _ in range(6):
        assert not looting.ring_due(NEAR, low)  # seen, but HP first
        clock.t += 0.125
    assert looting.should_loot(low) and not looting.loot_first(low)  # a fight goes on
    assert looting.step(low) is True  # else it stands still and heals
    assert looting.ring_due(NEAR, WS)


def test_no_ring_loot_while_the_client_is_unfocused() -> None:
    inp, clock = FakeInput(), Clock()
    looting = make_ring(clock, inp=inp)
    unfocused = WorldState(client_focused=False, screen_ok=True, hp_percent=100.0)
    for _ in range(8):
        assert not looting.ring_due(NEAR, unfocused)
        clock.t += 0.125
    assert inp.sent_lines == []


def test_a_ring_2_tiles_away_is_walked_to_with_no_fight_on() -> None:
    inp, clock = FakeInput(), Clock()
    looting = make_ring(clock, inp=inp, x=912)  # 2 tiles east
    for _ in range(8):
        assert not looting.ring_due(NEAR, WS)  # out of the hotkey's reach
        clock.t += 0.125
    assert not looting.loot_first(WS)  # a fight goes first
    assert looting.should_loot(WS) and looting.step(WS) is True
    assert looting.step(WS) is True  # one step at a time
    assert inp.sent_lines == ["K d"]
    clock.t += RING_STEP_SECONDS
    looting.ring_due(NEAR, WS)
    looting.step(WS)
    assert inp.sent_lines == ["K d", "K d"]


def test_a_ring_2_tiles_away_that_is_never_reached_is_left() -> None:
    inp, clock = FakeInput(), Clock()
    looting = make_ring(clock, inp=inp, x=912)  # a wall in the way: the steps do nothing
    for _ in range(40):
        looting.ring_due(NEAR, WS)
        if looting.should_loot(WS):
            looting.step(WS)
        clock.t += 0.125
    assert inp.sent_lines == ["K d"] * 3 and not looting.should_loot(WS)


def test_far_ring_is_not_looted() -> None:
    inp, clock = FakeInput(), Clock()
    looting = make_ring(clock, inp=inp, x=1000)  # 3 tiles east
    for _ in range(8):
        assert not looting.ring_due(NEAR, WS) and not looting.should_loot(WS)
        clock.t += 0.125
