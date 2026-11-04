"""Unit tests for moonlight.looting.looting: loot each ringed corpse, then wait."""

from __future__ import annotations

import numpy as np
import pytest

import moonlight.looting.looting as looting_module
from moonlight.core.state import WorldState
from moonlight.hardware.input import ClickSide, FakeInput
from moonlight.looting.looting import (
    RING_CONFIRM_SECONDS,
    RING_GONE_SECONDS,
    RING_GONE_TIMEOUT,
    RING_WALK_S_PER_TILE,
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
NEAR = range(1, 2)  # the 8 tiles around the player
WS = WorldState(client_focused=True, screen_ok=True, hp_percent=100.0)


class FakeClicker:
    """Records right-clicks; a click is sent after *steps* steps."""

    def __init__(self, steps: int = 2) -> None:
        self.clicks: list[tuple[tuple[int, int], ClickSide]] = []
        self.steps, self.left = steps, 0

    @property
    def busy(self) -> bool:
        return self.left > 0

    def request(self, target: tuple[int, int], *, side: ClickSide = ClickSide.LEFT) -> None:
        self.clicks.append((target, side))
        self.left = self.steps

    def step(self) -> bool | None:
        self.left -= 1
        return True if self.left == 0 else None


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
    clock: Clock, clicker: FakeClicker | None = None, flash: bool = False, x: int = 800,
    inp: FakeInput | None = None, **cfg: object,
) -> Looting:
    config = LootingConfig(**{"wait_ms": 400, **cfg})  # type: ignore[arg-type]
    return Looting(
        inp or FakeInput(), config, clock=clock, frames=RingFrames(flash, x),
        game_region=GRID, clicker=clicker,  # type: ignore[arg-type]
    )


def hide_ring(looting: Looting) -> None:
    looting._frames.gone = True  # type: ignore[union-attr]


def see_ring(looting: Looting, clock: Clock, reach: range = NEAR, snap: WorldState = WS) -> None:
    """Looks every 0.125 s until the ring is due; a ring counts only after turning a while."""
    for _ in range(10):
        if looting.ring_due(reach, snap):
            return
        clock.t += 0.125
    raise AssertionError("the ring was never due")


def loot_and_land(looting: Looting) -> None:
    looting.press_ring()
    looting.step(WS)  # the click goes out
    looting.step(WS)  # the loot lands


def test_ring_is_right_clicked_once_it_turned_then_wait_ms() -> None:
    clock, clicker = Clock(), FakeClicker(steps=1)
    looting = make_ring(clock, clicker)
    see_ring(looting, clock)
    assert clock.t >= RING_CONFIRM_SECONDS  # it turned a while first
    looting.press_ring()
    assert clicker.clicks == [(EAST, ClickSide.RIGHT)]
    assert looting.loot_first(WS)  # the next attack waits
    assert looting.step(WS) is True
    assert looting.step(WS) is True
    landed = clock.t
    clock.t = landed + 0.39
    assert looting.loot_first(WS) and not looting.ring_due(NEAR, WS)
    clock.t = landed + 0.41
    assert not looting.ring_due(NEAR, WS)  # landed, but the ring still turns
    assert looting.loot_first(WS) and looting.step(WS) is True
    hide_ring(looting)  # looted: the ring goes away
    clock.t += RING_GONE_SECONDS
    assert not looting.ring_due(NEAR, WS)
    assert not looting.loot_first(WS) and looting.step(WS) is False
    shots = sorted(p.name for p in looting_module.LOOT_SHOTS_DIR.glob("*.png"))
    assert [name.split("_")[1] for name in shots] == ["ring.png", "landed.png"]


def test_a_flash_seen_once_is_not_looted() -> None:
    clock, clicker = Clock(), FakeClicker(steps=1)
    looting = make_ring(clock, clicker, flash=True)  # shows for one look, then gone
    for _ in range(12):
        assert not looting.ring_due(NEAR, WS)
        clock.t += 0.125
    assert clicker.clicks == []


def test_a_creature_on_the_ring_tile_is_not_looted() -> None:
    clock, clicker = Clock(), FakeClicker(steps=1)
    looting = make_ring(clock, clicker)
    amazon_east = WorldState(
        client_focused=True, screen_ok=True, hp_percent=100.0, creature_slots=((8, 5),)
    )
    for _ in range(8):
        assert not looting.ring_due(NEAR, amazon_east)  # its outfit sparkles like a ring
        clock.t += 0.125
    assert looting.ring_due(NEAR, WS)  # it walked off: the corpse's ring is looted


def test_a_ring_still_there_after_the_timeout_is_looted_again() -> None:
    clock, clicker = Clock(), FakeClicker(steps=1)
    looting = make_ring(clock, clicker)
    see_ring(looting, clock)
    loot_and_land(looting)
    give_up_at = clock.t + 0.4 + RING_GONE_TIMEOUT
    while clock.t < give_up_at:
        assert not looting.ring_due(NEAR, WS) and looting.loot_first(WS)  # wait for it to go
        clock.t += 0.125
    see_ring(looting, clock)  # it still turns: looted again
    looting.press_ring()
    assert len(clicker.clicks) == 2


def test_hotkey_setting_presses_the_loot_hotkey_for_a_ring() -> None:
    inp, clock, clicker = FakeInput(), Clock(), FakeClicker()
    looting = make_ring(clock, clicker, inp=inp, loot_by_right_click=False)
    see_ring(looting, clock)
    looting.press_ring()
    assert inp.sent_lines == ["K alt+q"] and clicker.clicks == []
    hide_ring(looting)
    pressed = clock.t
    clock.t = pressed + RING_GONE_SECONDS - 0.01
    assert looting.step(WS) is True  # wait_ms, then the ring goes
    clock.t = pressed + RING_GONE_SECONDS + 0.01
    assert looting.step(WS) is False


def test_no_ring_does_nothing() -> None:
    inp, clock = FakeInput(), Clock()
    looting = Looting(inp, LootingConfig(), clock=clock)
    assert not looting.ring_due(NEAR, WS)
    assert not looting.should_loot(WS) and looting.step(WS) is False
    assert inp.sent_lines == []


def test_low_hp_holds_still_and_loots_once_hp_is_back() -> None:
    clock, clicker = Clock(), FakeClicker(steps=1)
    looting = make_ring(clock, clicker, min_hp_to_loot=70.0)
    low = WorldState(client_focused=True, screen_ok=True, hp_percent=50.0)
    for _ in range(6):
        assert not looting.ring_due(NEAR, low)  # seen, but HP first
        clock.t += 0.125
    assert looting.should_loot(low) and not looting.loot_first(low)  # a fight goes on
    assert looting.step(low) is True  # else it stands still and heals
    assert looting.ring_due(NEAR, WS)


def test_no_ring_loot_while_the_client_is_unfocused() -> None:
    clock, clicker = Clock(), FakeClicker()
    looting = make_ring(clock, clicker)
    unfocused = WorldState(client_focused=False, screen_ok=True, hp_percent=100.0)
    for _ in range(8):
        assert not looting.ring_due(NEAR, unfocused)
        clock.t += 0.125


def test_ring_click_holds_the_bot_until_it_is_sent() -> None:
    clock, clicker = Clock(), FakeClicker(steps=3)
    looting = make_ring(clock, clicker)
    see_ring(looting, clock)
    looting.press_ring()
    for _ in range(3):
        assert looting.should_loot(WS) and not looting.ring_due(NEAR, WS)
        assert looting.step(WS) is True  # the clicker moves, then clicks
    assert not clicker.busy


def test_ring_click_without_a_clicker_is_refused() -> None:
    clock = Clock()
    looting = make_ring(clock)
    see_ring(looting, clock)
    with pytest.raises(RuntimeError, match="needs a clicker"):
        looting.press_ring()


def test_far_ring_is_seen_only_in_reach_and_holds_for_the_walk() -> None:
    clock, clicker = Clock(), FakeClicker(steps=1)
    looting = make_ring(clock, clicker, x=1000)  # 3 tiles east
    for _ in range(8):
        assert not looting.ring_due(NEAR, WS)  # too far
        clock.t += 0.125
    assert looting.ring_due(range(1, 8), WS)
    loot_and_land(looting)
    hide_ring(looting)
    assert clicker.clicks == [((1008, 528), ClickSide.RIGHT)]
    landed = clock.t  # walks 2 tiles, then wait_ms
    clock.t = landed + 0.4 + 2 * RING_WALK_S_PER_TILE - 0.01
    assert looting.should_loot(WS) and not looting.ring_due(range(1, 8), WS)
    clock.t += 0.02
    assert not looting.should_loot(WS)
