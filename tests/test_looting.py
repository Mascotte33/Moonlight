"""Unit tests for moonlight.looting.looting: one loot-all press, then a wait."""

from __future__ import annotations

from moonlight.core.state import WorldState
from moonlight.hardware.input import FakeInput
from moonlight.looting.looting import PRESS_DELAY, Looting, LootingConfig


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
