"""Unit tests for moonlight.looting.looting: one loot-all press, then a wait."""

from __future__ import annotations

import numpy as np

from moonlight.core.state import WorldState
from moonlight.hardware.input import FakeInput
from moonlight.looting.looting import PRESS_DELAY, RING_WAIT, Looting, LootingConfig


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


def test_ring_press_is_wait_ms_apart_and_only_when_switched_on() -> None:
    inp, clock = FakeInput(), Clock()
    frames = [np.zeros((1056, 1440, 3), np.uint8) for _ in range(2)]
    frames[0][500:505, 800:808] = frames[1][520:525, 820:828] = (255, 230, 200)  # a ring turning
    tick = iter(range(100))

    def frame() -> np.ndarray:
        return frames[next(tick) % 2]

    def make_ring(**cfg: object) -> Looting:
        config = LootingConfig(wait_ms=400, **cfg)  # type: ignore[arg-type]
        return Looting(inp, config, clock=clock, frames=frame, game_region=GRID)

    assert not make_ring().ring_due()  # off by default
    looting = make_ring(ringed_corpses=True)
    assert not looting.ring_due()  # the first look
    clock.t = 0.1
    assert looting.ring_due()
    looting.press_ring()
    assert inp.sent_lines == ["K alt+q"]
    clock.t = 0.3
    assert not looting.ring_due()  # wait_ms after the press
    clock.t = 0.5
    assert not looting.ring_due()  # and at least RING_GAP
    clock.t = 0.6
    assert looting.ring_due()


def test_ring_only_kill_holds_still_until_the_ring_press_lands() -> None:
    looting, inp, clock, ws = make(after_kill=False, ringed_corpses=True, wait_ms=400)
    looting.on_target_died("Wolf")
    assert looting.should_loot(ws) and not looting.loot_first(ws)  # attacking goes on
    assert looting.step(ws) is True and inp.sent_lines == []  # stands still, no kill press
    clock.t = 0.2
    looting.press_ring()
    clock.t = 0.59
    assert looting.step(ws) is True  # the loot lands
    clock.t = 0.61
    assert not looting.should_loot(ws) and looting.step(ws) is False


def test_ring_only_kill_without_a_ring_walks_on_after_ring_wait() -> None:
    looting, _, clock, ws = make(after_kill=False, ringed_corpses=True)
    looting.on_target_died("Wolf")
    clock.t = RING_WAIT
    assert not looting.should_loot(ws) and looting.step(ws) is False


def test_ring_press_while_walking_stops_for_wait_ms() -> None:
    looting, _, clock, ws = make(ringed_corpses=True, wait_ms=400)
    clock.t = 5.0
    looting.press_ring()  # a ring passed on the way, no kill
    assert looting.should_loot(ws) and looting.step(ws) is True
    clock.t = 5.41
    assert not looting.should_loot(ws)
