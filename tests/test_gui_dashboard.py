"""Dashboard stat card text and health dots (T1.F1.K1, T1.F2.K1)."""

from __future__ import annotations

import pytest

from moonlight.core.state import Position, WorldState
from moonlight.gui.dashboard_tab import card_values, uptime_text
from moonlight.gui.tab_base import health


def test_uptime_text() -> None:
    assert uptime_text({"uptime_seconds": 3725.9}) == "1:02:05"
    assert uptime_text({"uptime_seconds": 0.0}) == "0:00:00"


def test_stopped_bot_shows_dash() -> None:
    values = card_values({}, WorldState())
    assert values["Uptime"] == values["Ticks"] == values["Stamina"] == "—"


def test_card_values_from_stats() -> None:
    values = card_values({"uptime_seconds": 61, "tick_count": 42}, WorldState())
    assert values["Ticks"] == "42" and values["Uptime"] == "0:01:01"
    assert card_values({"stamina_minutes": 2463}, WorldState())["Stamina"] == "41:03"


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


# doc: docs/prs/near-targeting.md#machine-line
def test_public_ip_is_the_reply_text(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import urllib.request

    from moonlight.gui import dashboard_tab

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: io.BytesIO(b"1.2.3.4\n"))
    assert dashboard_tab.public_ip() == "1.2.3.4"


def test_hardware_id_is_the_mac_address() -> None:
    from moonlight.gui.dashboard_tab import hardware_id

    assert hardware_id(0x0A1B2C3D4E5F) == "0a:1b:2c:3d:4e:5f"
    with pytest.raises(OSError):
        hardware_id(0x010000000000)  # the random-node bit: no card found
