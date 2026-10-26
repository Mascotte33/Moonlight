"""Top-bar text formatting (S.F2.K1) — plain function, no Tk."""

from __future__ import annotations

from moonlight.core.loop import BotState
from moonlight.core.state import WorldState
from moonlight.gui.state_bridge import topbar_text


def test_hp_mp_percent_text() -> None:
    snap = WorldState()
    snap.hp_percent = 73.0
    snap.mp_percent = 40.4
    hp, mp, _ = topbar_text(snap, True, BotState.IDLE)
    assert (hp, mp) == ("73%", "40%")


def test_state_text() -> None:
    snap = WorldState()
    assert topbar_text(snap, False, None)[2] == "Stopped"
    assert topbar_text(snap, False, BotState.PAUSED)[2] == "Stopped"
    assert topbar_text(snap, True, BotState.IDLE)[2] == "Running"
    assert topbar_text(snap, True, BotState.PAUSED)[2] == "Waiting for game"
    assert topbar_text(snap, True, BotState.PAUSED, paused=True)[2] == "Paused"
