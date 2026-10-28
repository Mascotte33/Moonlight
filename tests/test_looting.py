"""Unit tests for moonlight.looting.looting: one loot-all press, then a wait."""

from __future__ import annotations

from moonlight.core.state import WorldState
from moonlight.hardware.input import FakeInput
from moonlight.looting.looting import Looting, LootingConfig


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
    assert looting.step(ws) is True
    clock.t = 0.3
    assert looting.step(ws) is True
    clock.t = 0.41
    assert looting.step(ws) is False
    assert inp.sent_lines == ["K alt+q"] and looting.pending is None


def test_battle_list_emptied_queues_a_press() -> None:
    looting, inp, _, ws = make()
    looting.on_target_died("")
    looting.step(ws)
    assert inp.sent_lines == ["K alt+q"]


def test_low_hp_skips_the_loot() -> None:
    looting, inp, _, ws = make(min_hp_to_loot=40.0)
    ws.hp_percent = 30.0
    looting.on_target_died("Wolf")
    assert looting.step(ws) is False
    assert inp.sent_lines == [] and looting.pending is None


def test_nothing_queued_does_nothing() -> None:
    looting, inp, _, ws = make()
    assert not looting.should_loot(ws) and looting.step(ws) is False
    assert inp.sent_lines == []
