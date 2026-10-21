"""Dashboard stat card text and health dots (T1.F1.K1, T1.F2.K1)."""

from __future__ import annotations

from moonlight.bot.state import Position, WorldState
from moonlight.gui.tabs.base import health
from moonlight.gui.tabs.dashboard import card_values, uptime_text


def test_uptime_text() -> None:
    assert uptime_text({"uptime_seconds": 3725.9}) == "1:02:05"
    assert uptime_text({"uptime_seconds": 0.0}) == "0:00:00"


def test_stopped_bot_shows_dash() -> None:
    values = card_values({}, WorldState())
    assert values["Uptime"] == values["Ticks"] == "—"


def test_card_values_from_stats() -> None:
    values = card_values({"uptime_seconds": 61, "tick_count": 42}, WorldState())
    assert values["Ticks"] == "42" and values["Uptime"] == "0:01:01"


def _dots(snap: WorldState, arduino: bool = False, running: bool = False) -> dict[str, bool]:
    return dict(health(snap, arduino, running, now=100.0))


def test_every_dot_red_when_nothing_runs() -> None:
    assert not any(_dots(WorldState()).values())


def test_each_dot_from_world_state() -> None:
    snap = WorldState(screen_ok=True, last_update=99.0, position=Position(1024, 980, 7))
    assert _dots(snap, arduino=True, running=True) == dict.fromkeys(
        ("Screen", "Vitals", "Minimap", "Arduino", "Bot"), True
    )


def test_stale_vitals_are_red() -> None:
    assert not _dots(WorldState(last_update=97.0))["Vitals"]
