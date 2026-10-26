"""Unit tests for moonlight.refill.refill (T24).

Offline: a fake cavebot that "arrives" when told to, a real
RouteFileLoader over a temp route file, hotkeys go to FakeInput.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from moonlight.cavebot.cavebot import Cavebot, Route, RouteFileLoader
from moonlight.core.state import Capacity, WorldState
from moonlight.hardware.input import FakeInput
from moonlight.looting.looting import Looting
from moonlight.refill.refill import (
    DEPOSIT_RETRY_TICKS,
    DEPOSIT_TIMEOUT_TICKS,
    Refill,
    RefillConfig,
    RefillPhase,
)

ROUTE: dict[str, Any] = {
    "name": "refill",
    "waypoints": [
        {"x": 100, "y": 100, "z": 7},
        {"x": 110, "y": 100, "z": 7},
        {"action": "label", "label": "storage"},
        {"x": 100, "y": 100, "z": 7},
        {"action": "label", "label": "farming"},
        {"action": "goto", "label": "farming"},  # ignored: past 'farming'
    ],
}


class FakeCavebot:
    def __init__(self) -> None:
        self.route = Route(name="hunt")
        self.alarm: str | None = None
        self.active = False
        self.steps = 0
        self.arrive_after = 3  # steps until the current leg completes

    def start(self) -> None:
        self.active, self.alarm, self.steps = True, None, 0

    def stop(self) -> None:
        self.active = False

    def is_active(self) -> bool:
        return self.active

    def step(self) -> None:
        self.steps += 1
        if self.steps >= self.arrive_after:
            self.active = False


class FakeLooting:
    pending: str | None = None


class Sim:
    def __init__(self, tmp_path: Path, route: object = ROUTE, **config: Any) -> None:
        (tmp_path / "refill.json").write_text(json.dumps(route), encoding="utf-8")
        self.cavebot = FakeCavebot()
        self.looting = FakeLooting()
        self.fake = FakeInput()
        self.state = WorldState(client_focused=True, screen_ok=True)
        self.state.capacity = Capacity(weight=95, max_weight=100)
        self.refill = Refill(
            RefillConfig(routes_dir=tmp_path, **config),
            cast(Cavebot, self.cavebot),
            RouteFileLoader(),
            cast(Looting, self.looting),
            self.state,
            self.fake,
        )

    def travel(self) -> None:
        for _ in range(self.cavebot.arrive_after):
            assert self.refill.step(self.state) is True


def phase(sim: Sim) -> RefillPhase:
    """Read the phase via a call so mypy does not narrow it across steps."""
    return sim.refill.phase


def test_config_defaults() -> None:
    c = RefillConfig()
    assert (c.trigger_cap, c.stop_cap) == (90.0, 10.0)
    assert (c.route_name, c.storage_hotkey, c.deposit_hotkey) == ("refill", "F8", None)
    assert c.routes_dir == Path("data/waypoints")


def test_should_refill_by_cap(tmp_path: Path) -> None:
    sim = Sim(tmp_path)
    assert sim.refill.should_refill(sim.state)  # 95% >= 90%
    sim.state.capacity = Capacity(weight=90, max_weight=100)
    assert sim.refill.should_refill(sim.state)
    sim.state.capacity = Capacity(weight=89, max_weight=100)
    assert not sim.refill.should_refill(sim.state)
    sim.state.capacity = Capacity()  # unknown cap
    assert not sim.refill.should_refill(sim.state)


def test_should_refill_waits_for_pending_loot(tmp_path: Path) -> None:
    sim = Sim(tmp_path)
    sim.looting.pending = "rat"
    assert not sim.refill.should_refill(sim.state)


def test_no_route_file_means_no_refill(tmp_path: Path) -> None:
    sim = Sim(tmp_path, route_name="missing")
    assert not sim.refill.should_refill(sim.state)
    sim.refill.start()
    assert phase(sim) is RefillPhase.IDLE
    assert not sim.cavebot.active


@pytest.mark.parametrize(
    "waypoints",
    [
        [{"x": 1, "y": 1, "z": 7}, {"action": "label", "label": "storage"}],
        [
            {"action": "label", "label": "farming"},
            {"x": 1, "y": 1, "z": 7},
            {"action": "label", "label": "storage"},
        ],
    ],
)
def test_route_without_valid_labels_means_no_refill(tmp_path: Path, waypoints: list[Any]) -> None:
    sim = Sim(tmp_path, route={"name": "refill", "waypoints": waypoints})
    assert not sim.refill.should_refill(sim.state)


def test_full_trip(tmp_path: Path) -> None:
    sim = Sim(tmp_path, deposit_hotkey="F11")
    hunt = sim.cavebot.route
    assert not sim.refill.is_active()  # IDLE

    sim.refill.start()
    assert phase(sim) is RefillPhase.TRAVEL_TO_STORAGE
    assert sim.refill.is_active()
    assert [wp.action for wp in sim.cavebot.route.waypoints] == ["walk", "walk", "label"]
    sim.travel()  # cavebot steps until the leg completes
    assert sim.cavebot.steps == 3
    assert phase(sim) is RefillPhase.DEPOSIT

    assert sim.refill.step(sim.state) is True  # open storage
    assert sim.refill.step(sim.state) is True  # deposit all
    assert sim.fake.sent_lines == ["K F8", "K F11"]
    sim.state.capacity = Capacity(weight=5, max_weight=100)
    assert sim.refill.step(sim.state) is True  # cap low -> close, head back
    assert sim.fake.sent_lines == ["K F8", "K F11", "K F8"]
    assert phase(sim) is RefillPhase.TRAVEL_BACK
    assert [wp.action for wp in sim.cavebot.route.waypoints] == ["walk", "label"]

    for _ in range(sim.cavebot.arrive_after - 1):
        assert sim.refill.step(sim.state) is True
    assert sim.refill.step(sim.state) is False  # back at the farming spot
    assert phase(sim) is RefillPhase.DONE
    assert not sim.refill.is_active()
    assert not sim.cavebot.active
    assert sim.cavebot.route is hunt  # previous route restored
    assert sim.refill.step(sim.state) is False


def test_deposit_retries_hotkey(tmp_path: Path) -> None:
    sim = Sim(tmp_path, deposit_hotkey="F11")
    sim.refill.start()
    sim.travel()
    for _ in range(DEPOSIT_RETRY_TICKS + 2):
        sim.refill.step(sim.state)
    assert sim.fake.sent_lines == ["K F8", "K F11", "K F11"]


def test_deposit_fails_closed_when_cap_never_drops(tmp_path: Path) -> None:
    sim = Sim(tmp_path)  # no deposit hotkey: nothing can move the items
    hunt = sim.cavebot.route
    sim.refill.start()
    sim.travel()
    for _ in range(DEPOSIT_TIMEOUT_TICKS):
        assert sim.refill.step(sim.state) is True
    assert sim.refill.step(sim.state) is False
    assert sim.fake.sent_lines == ["K F8", "K F8"]  # opened, closed
    assert sim.refill.alarm == "deposit_failed"
    assert phase(sim) is RefillPhase.IDLE
    assert sim.cavebot.route is hunt
    assert not sim.refill.should_refill(sim.state)  # no endless retry
    sim.refill.start()  # a manual start clears the alarm
    assert sim.refill.alarm is None


def test_deposit_sends_nothing_without_focus(tmp_path: Path) -> None:
    sim = Sim(tmp_path)
    sim.refill.start()
    sim.travel()
    sim.state.client_focused = False
    assert sim.refill.step(sim.state) is True
    assert sim.fake.sent_lines == []


def test_cavebot_alarm_stops_refill(tmp_path: Path) -> None:
    sim = Sim(tmp_path)
    sim.refill.start()
    sim.cavebot.step = lambda: setattr(sim.cavebot, "active", False)  # type: ignore[method-assign]
    sim.cavebot.alarm = "stuck"
    assert sim.refill.step(sim.state) is False
    assert sim.refill.alarm == "stuck"
    assert phase(sim) is RefillPhase.IDLE


def test_stop_mid_refill(tmp_path: Path) -> None:
    sim = Sim(tmp_path)
    hunt = sim.cavebot.route
    sim.refill.start()
    sim.refill.step(sim.state)
    sim.refill.stop()
    assert phase(sim) is RefillPhase.IDLE
    assert not sim.refill.is_active()
    assert not sim.cavebot.active
    assert sim.cavebot.route is hunt
    assert sim.refill.step(sim.state) is False
