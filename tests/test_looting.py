"""Unit tests for moonlight.looting.looting: loot each ringed corpse, then wait."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

import moonlight.looting.looting as looting_module
from moonlight.core.state import WorldState
from moonlight.hardware.input import ClickSide, FakeInput
from moonlight.looting.looting import RING_WALK_S_PER_TILE, Looting, LootingConfig


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


def ring_frames(still_after: bool = False, x: int = 800) -> Callable[[], np.ndarray]:
    frames = [np.zeros((1056, 1440, 3), np.uint8) for _ in range(2)]
    frames[0][500:505, x : x + 8] = frames[1][520:525, x + 20 : x + 28] = (160, 178, 205)  # turning
    tick = iter(range(100))
    if still_after:
        frames.append(frames[1])  # then the ring stands still for a look
        return lambda: frames[min(next(tick), 2)]
    return lambda: frames[next(tick) % 2]


def make_ring(
    clock: Clock, clicker: FakeClicker | None = None, still_after: bool = False, x: int = 800,
    inp: FakeInput | None = None, **cfg: object,
) -> Looting:
    config = LootingConfig(**{"wait_ms": 400, **cfg})  # type: ignore[arg-type]
    return Looting(
        inp or FakeInput(), config, clock=clock, frames=ring_frames(still_after, x),
        game_region=GRID, clicker=clicker,  # type: ignore[arg-type]
    )


def see_ring(looting: Looting, clock: Clock, reach: range = NEAR) -> None:
    """Two looks: the ring is seen turning."""
    looting.ring_due(reach, WS)
    clock.t += 0.1
    assert looting.ring_due(reach, WS)


def test_ring_is_right_clicked_at_once_then_wait_ms() -> None:
    clock, clicker = Clock(), FakeClicker(steps=1)
    looting = make_ring(clock, clicker)
    assert not looting.ring_due(NEAR, WS)  # the first look
    clock.t = 0.1
    assert looting.ring_due(NEAR, WS)
    looting.press_ring()
    assert clicker.clicks == [(EAST, ClickSide.RIGHT)]
    assert looting.loot_first(WS)  # the next attack waits
    assert looting.step(WS) is True  # the click goes out at 0.1
    assert looting.step(WS) is True  # the loot lands
    clock.t = 0.49
    assert looting.loot_first(WS) and not looting.ring_due(NEAR, WS)
    clock.t = 0.51
    assert not looting.loot_first(WS) and looting.step(WS) is False
    looting.ring_due(NEAR, WS)
    shots = sorted(p.name for p in looting_module.LOOT_SHOTS_DIR.glob("*.png"))
    assert [name.split("_")[1] for name in shots] == ["ring.png", "landed.png"]


def test_next_ring_is_looted_after_the_wait_until_none_is_left() -> None:
    clock, clicker = Clock(), FakeClicker(steps=1)
    looting = make_ring(clock, clicker)
    see_ring(looting, clock)
    looting.press_ring()
    looting.step(WS)
    looting.step(WS)
    clock.t += 0.41
    assert looting.ring_due(NEAR, WS)  # another corpse: looted too
    looting.press_ring()
    assert len(clicker.clicks) == 2


def test_hotkey_setting_presses_the_loot_hotkey_for_a_ring() -> None:
    inp, clock, clicker = FakeInput(), Clock(), FakeClicker()
    looting = make_ring(clock, clicker, inp=inp, loot_by_right_click=False)
    see_ring(looting, clock)
    looting.press_ring()
    assert inp.sent_lines == ["K alt+q"] and clicker.clicks == []
    clock.t += 0.39
    assert looting.step(WS) is True  # wait_ms
    clock.t += 0.02
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
    looting.ring_due(NEAR, low)
    clock.t = 0.1
    assert not looting.ring_due(NEAR, low)  # seen, but HP first
    assert looting.should_loot(low) and not looting.loot_first(low)  # a fight goes on
    assert looting.step(low) is True  # else it stands still and heals
    clock.t = 0.2
    assert looting.ring_due(NEAR, WS)


def test_no_ring_loot_while_the_client_is_unfocused() -> None:
    clock, clicker = Clock(), FakeClicker()
    looting = make_ring(clock, clicker)
    unfocused = WorldState(client_focused=False, screen_ok=True, hp_percent=100.0)
    looting.ring_due(NEAR, unfocused)
    clock.t = 0.1
    assert not looting.ring_due(NEAR, unfocused)


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
    looting.ring_due(NEAR, WS)
    clock.t = 0.1
    assert not looting.ring_due(NEAR, WS)  # too far
    clock.t = 0.2
    assert looting.ring_due(range(1, 8), WS)
    looting.press_ring()
    assert clicker.clicks == [((1008, 528), ClickSide.RIGHT)]
    looting.step(WS)
    looting.step(WS)  # landed at 0.2: walks 2 tiles, then wait_ms
    clock.t = 0.2 + 0.4 + 2 * RING_WALK_S_PER_TILE - 0.01
    assert looting.should_loot(WS) and not looting.ring_due(range(1, 8), WS)
    clock.t += 0.02
    assert not looting.should_loot(WS)


def test_short_wait_ms_lets_the_fight_go_on_before_the_ring_gap() -> None:
    clock, clicker = Clock(), FakeClicker(steps=1)
    looting = make_ring(clock, clicker, wait_ms=100)
    see_ring(looting, clock)
    looting.press_ring()
    looting.step(WS)
    looting.step(WS)  # sent at 0.1
    clock.t = 0.21
    assert not looting.loot_first(WS)  # RING_GAP (0.3 s) only spaces ring loots
    assert not looting.ring_due(NEAR, WS)
