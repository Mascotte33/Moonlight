"""Unit tests for moonlight.looting.looting: one loot-all press, then a wait."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from moonlight.core.state import WorldState
from moonlight.hardware.input import ClickSide, FakeInput
from moonlight.looting.looting import (
    PRESS_DELAY,
    RING_WAIT,
    RING_WALK_S_PER_TILE,
    Looting,
    LootingConfig,
)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make(**cfg: object) -> tuple[Looting, FakeInput, Clock, WorldState]:
    inp, clock = FakeInput(), Clock()
    looting = Looting(inp, LootingConfig(**cfg), clock=clock)  # type: ignore[arg-type]
    ws = WorldState(client_focused=True, screen_ok=True, hp_percent=100.0)
    return looting, inp, clock, ws


def test_press_once_then_hold_for_wait_ms() -> None:
    looting, inp, clock, ws = make(wait_ms=400)
    looting.on_target_died("Wolf")
    assert looting.should_loot(ws)
    assert looting.step(ws) is True  # too soon after the kill: no press yet
    assert inp.sent_lines == []
    clock.t = PRESS_DELAY
    assert looting.step(ws) is True
    clock.t = PRESS_DELAY + 0.3
    assert looting.step(ws) is True
    clock.t = PRESS_DELAY + 0.41
    assert looting.step(ws) is False
    assert inp.sent_lines == ["K alt+q"] and looting.pending is None


def test_battle_list_emptied_queues_a_press() -> None:
    looting, inp, clock, ws = make()
    looting.on_target_died("")
    clock.t = PRESS_DELAY
    looting.step(ws)
    assert inp.sent_lines == ["K alt+q"]


def test_low_hp_waits_for_hp_then_loots() -> None:
    looting, inp, clock, ws = make(min_hp_to_loot=70.0)
    ws.hp_percent = 50.0
    looting.on_target_died("Wolf")
    clock.t = PRESS_DELAY
    assert looting.step(ws) is True  # holds the bot while it heals
    assert inp.sent_lines == [] and looting.pending == "Wolf"
    ws.hp_percent = 70.0
    assert looting.step(ws) is True
    assert inp.sent_lines == ["K alt+q"]


def test_nothing_queued_does_nothing() -> None:
    looting, inp, _, ws = make()
    assert not looting.should_loot(ws) and looting.step(ws) is False
    assert inp.sent_lines == []


def test_loot_first_unless_hp_low() -> None:
    looting, _, _, ws = make(min_hp_to_loot=70.0)
    assert not looting.loot_first(ws)
    looting.on_target_died("Wolf")
    assert looting.loot_first(ws)
    ws.hp_percent = 50.0
    assert not looting.loot_first(ws)  # keep fighting; loot after


def test_after_kill_off_ignores_kills() -> None:
    looting, inp, _, ws = make(after_kill=False)
    looting.on_target_died("Wolf")
    assert not looting.should_loot(ws) and looting.step(ws) is False
    assert inp.sent_lines == []


GRID = (0, 0, 1440, 1056)  # 96 px tiles; the player's tile is x 672-767, y 480-575
EAST = (816, 528)  # the middle of the tile east of the player
NEAR = range(1, 2)  # the 8 tiles around the player


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
    clock: Clock, clicker: FakeClicker, still_after: bool = False, x: int = 800, **cfg: object
) -> Looting:
    config = LootingConfig(wait_ms=400, **cfg)  # type: ignore[arg-type]
    return Looting(
        FakeInput(), config, clock=clock, frames=ring_frames(still_after, x), game_region=GRID,
        clicker=clicker,  # type: ignore[arg-type]
    )


def test_ring_click_is_a_right_click_wait_ms_apart_and_only_when_switched_on() -> None:
    clock, clicker = Clock(), FakeClicker(steps=1)
    assert not make_ring(clock, clicker).ring_due(NEAR)  # off by default
    looting = make_ring(clock, clicker, ringed_corpses=True)
    ws = WorldState(client_focused=True, screen_ok=True, hp_percent=100.0)
    assert not looting.ring_due(NEAR)  # the first look
    clock.t = 0.1
    assert looting.ring_due(NEAR)
    looting.press_ring()
    assert clicker.clicks == [(EAST, ClickSide.RIGHT)]
    assert looting.step(ws) is True  # the click goes out
    assert looting.step(ws) is True  # the loot lands
    clock.t = 0.3
    assert not looting.ring_due(NEAR)  # wait_ms (at least RING_GAP) after the click
    clock.t = 0.5
    assert looting.ring_due(NEAR)


def test_ring_click_holds_the_bot_until_it_is_sent_then_wait_ms() -> None:
    clock, clicker = Clock(), FakeClicker(steps=3)
    looting = make_ring(clock, clicker, ringed_corpses=True, after_kill=False)
    ws = WorldState(client_focused=True, screen_ok=True, hp_percent=100.0)
    looting.ring_due(NEAR)
    clock.t = 5.0
    assert looting.ring_due(NEAR)
    looting.press_ring()  # a ring passed on the way, no kill
    for _ in range(3):
        assert looting.should_loot(ws) and not looting.ring_due(NEAR)
        assert looting.step(ws) is True  # the clicker moves, then clicks
    assert not clicker.busy
    assert looting.step(ws) is True  # the loot lands
    clock.t = 5.39
    assert looting.should_loot(ws)
    clock.t = 5.41
    assert not looting.should_loot(ws) and looting.step(ws) is False


def test_ring_only_kill_holds_still_while_it_looks_for_the_ring() -> None:
    looting, inp, clock, ws = make(after_kill=False, ringed_corpses=True, wait_ms=400)
    looting.on_target_died("Wolf")
    assert looting.should_loot(ws) and not looting.loot_first(ws)  # attacking goes on
    assert looting.step(ws) is True and inp.sent_lines == []  # stands still, no kill press


def test_ring_only_kill_without_a_ring_walks_on_after_ring_wait() -> None:
    looting, _, clock, ws = make(after_kill=False, ringed_corpses=True)
    looting.on_target_died("Wolf")
    clock.t = RING_WAIT
    assert not looting.should_loot(ws) and looting.step(ws) is False


def test_seen_ring_stays_due_until_its_click_is_asked_for() -> None:
    clock, clicker = Clock(), FakeClicker()
    looting = make_ring(clock, clicker, still_after=True, ringed_corpses=True)
    assert not looting.ring_due(NEAR)
    clock.t = 0.1
    assert looting.ring_due(NEAR)  # the input limit drops this click
    clock.t = 0.2
    assert looting.ring_due(NEAR)  # still due, though this look sees no change
    looting.press_ring()
    assert not looting.ring_due(NEAR)


def test_ring_click_without_a_clicker_is_refused() -> None:
    looting, *_ = make(ringed_corpses=True)
    with pytest.raises(RuntimeError, match="needs a clicker"):
        looting.press_ring()


def test_far_ring_is_seen_only_in_reach_and_holds_for_the_walk() -> None:
    clock, clicker = Clock(), FakeClicker(steps=1)
    looting = make_ring(clock, clicker, x=1000, ringed_corpses=True)  # 3 tiles east
    ws = WorldState(client_focused=True, screen_ok=True, hp_percent=100.0)
    looting.ring_due(NEAR)
    clock.t = 0.1
    assert not looting.ring_due(NEAR)  # mid-fight: too far
    clock.t = 0.2
    assert looting.ring_due(range(1, 8))  # nothing to kill: the whole grid
    looting.press_ring()
    assert clicker.clicks == [((1008, 528), ClickSide.RIGHT)]
    looting.step(ws)
    looting.step(ws)  # landed at 0.2: walks 2 tiles, then wait_ms
    clock.t = 0.2 + 0.4 + 2 * RING_WALK_S_PER_TILE - 0.01
    assert looting.should_loot(ws) and not looting.ring_due(range(1, 8))
    clock.t += 0.02
    assert not looting.should_loot(ws)


def test_kill_with_a_known_tile_right_clicks_its_corpse() -> None:
    clock, clicker = Clock(), FakeClicker(steps=1)
    looting = Looting(FakeInput(), LootingConfig(), clock=clock, game_region=GRID, clicker=clicker)  # type: ignore[arg-type]
    ws = WorldState(client_focused=True, screen_ok=True, hp_percent=100.0)
    looting.on_target_died("Wolf", (8, 5))
    assert looting.step(ws) is True  # at once, no PRESS_DELAY
    assert clicker.clicks == [(EAST, ClickSide.RIGHT)]
    assert looting.step(ws) is True  # the click goes out
    assert looting.step(ws) is True  # the loot lands: wait_ms
    clock.t = 0.39
    assert looting.should_loot(ws)
    clock.t = 0.41
    assert not looting.should_loot(ws)


def test_hotkey_setting_presses_the_loot_hotkey_instead() -> None:
    inp, clock, clicker = FakeInput(), Clock(), FakeClicker()
    config = LootingConfig(loot_by_right_click=False)
    looting = Looting(inp, config, clock=clock, game_region=GRID, clicker=clicker)  # type: ignore[arg-type]
    looting.on_target_died("Wolf", (8, 5))
    clock.t = PRESS_DELAY
    looting.step(WorldState(client_focused=True, screen_ok=True, hp_percent=100.0))
    assert inp.sent_lines == ["K alt+q"] and clicker.clicks == []


def test_corpse_click_holds_the_fight_until_the_loot_lands() -> None:
    clock, clicker = Clock(), FakeClicker(steps=2)
    config = LootingConfig(wait_ms=400)
    looting = Looting(FakeInput(), config, clock=clock, game_region=GRID, clicker=clicker)  # type: ignore[arg-type]
    ws = WorldState(client_focused=True, screen_ok=True, hp_percent=100.0)
    looting.on_target_died("Wolf", (8, 5))
    clock.t = PRESS_DELAY
    looting.step(ws)  # the right-click is asked for
    assert looting.loot_first(ws)  # the next target waits for the click
    looting.step(ws)
    looting.step(ws)  # sent
    looting.step(ws)
    assert looting.loot_first(ws)  # and for the loot to land
    clock.t = PRESS_DELAY + 0.41
    assert not looting.loot_first(ws)
