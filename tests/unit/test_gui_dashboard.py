"""Dashboard stat card text (T1.F1.K1)."""

from __future__ import annotations

from moonlight.bot.state import WorldState
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
