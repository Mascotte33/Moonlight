"""T29: safety invariants the bot must always hold (one test per invariant).

Offline: ``FakeInput`` captures every wire line, ``WorldState`` is set by hand,
the loop clock is fake. ``python -m pytest tests/safety/ -v``.

Tests 13-18 (T32a / F-19) run the production wiring instead: ``cli.build_loop``
+ ``make_perceive`` over a real golden frame with a toggleable focus check.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest import mock

import cv2
import numpy as np
import pytest

from moonlight.calibrate import CalibrationProfile, default_profiles_dir
from moonlight.cli import build_loop
from moonlight.clicker import ClickController
from moonlight.config import BotConfig
from moonlight.decision.cavebot import Cavebot, Route, RouteFileLoader, Waypoint
from moonlight.decision.healing import HealingConfig, HealingDecision, HealType
from moonlight.decision.looting import Looting, LootingConfig
from moonlight.decision.refill import Refill, RefillConfig
from moonlight.input import FakeInput
from moonlight.loop import BotState, DecisionModules, F12Monitor, GameLoop, LoopConfig
from moonlight.perception.loot import LootWindow
from moonlight.state import Capacity, Monster, Position, WorldState

pytestmark = pytest.mark.safety


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, dt: float) -> None:
        self.now += max(0.0, dt)


def make_state(**fields: Any) -> WorldState:
    ws = WorldState()
    with ws.lock():
        ws.screen_ok = True
        ws.client_focused = True
        ws.hp_percent = 100.0
        for k, v in fields.items():
            setattr(ws, k, v)
    return ws


def make_loop(
    ws: WorldState, inp: FakeInput, *, f12: F12Monitor | None = None, **modules: Any
) -> tuple[GameLoop, Clock]:
    clock = Clock()
    gl = GameLoop(
        ws,
        inp,
        LoopConfig(enable_f12_monitor=f12 is not None),
        clock=clock,
        sleep_fn=clock.sleep,
        f12_monitor=f12,
        modules=DecisionModules(**modules),
    )
    return gl, clock


def idle_module(**overrides: Any) -> mock.Mock:
    m = mock.Mock()
    m.decide.return_value = None
    m.track.return_value = []
    m.should_loot.return_value = False
    m.should_refill.return_value = False
    m.is_active.return_value = False
    m.send.return_value = True
    m.alarm = None
    m.configure_mock(**overrides)
    return m


def test_1_kill_switch_f12_stops_loop_and_releases_all() -> None:
    ws = make_state(selected_target=Monster("Wolf"))
    inp = FakeInput()
    pressed = threading.Event()
    f12 = F12Monitor(lambda: None, poll_interval=0.001, key_state=lambda _vk: pressed.is_set())
    gl, _ = make_loop(ws, inp, f12=f12)
    f12._on_press = gl.kill  # the monitor drives the real kill switch
    f12.start()
    try:
        pressed.set()  # simulate the F12 press
        assert gl._kill_event.wait(1.0)
    finally:
        f12.stop()
    gl.run()  # the kill is already active -> run() returns immediately
    gl.tick()  # next tick processes the kill
    assert gl.current_state is BotState.KILL
    assert gl.stop_requested and gl.kill_switch_active
    assert inp.sent_lines == ["X"]


def test_2_fail_closed_when_window_unfocused() -> None:
    ws = make_state(client_focused=False, hp_percent=10.0, selected_target=Monster("Wolf"))
    inp = FakeInput()
    gl, _ = make_loop(ws, inp)
    for _ in range(5):
        gl.tick()
    assert gl.current_state is BotState.EMERGENCY_HEAL  # wanted to act...
    assert inp.sent_lines == []  # ...but sent nothing
    assert gl.get_stats()["actions_dropped_unfocused"] == 5


def test_3_fail_closed_when_screen_not_ok() -> None:
    ws = make_state(screen_ok=False, hp_percent=10.0, selected_target=Monster("Wolf"))
    inp = FakeInput()
    gl, _ = make_loop(ws, inp)
    for _ in range(5):
        gl.tick()
    assert gl.current_state is BotState.PAUSED  # loop pauses (F-05)...
    assert not gl.stop_requested
    assert inp.sent_lines == ["X"]  # ...releasing once and sending nothing else


def make_cavebot(
    clock: Clock, waypoints: list[Waypoint], pos: tuple[int, int], z: int
) -> tuple[Cavebot, WorldState]:
    state = make_state(screen_ok=True)
    state.position = Position(*pos, z)
    bot = Cavebot(
        Route(name="r", waypoints=waypoints),
        (0, 0, 100, 100),
        cast(ClickController, mock.Mock()),
        state,
        game_region=(0, 0, 150, 110),
        clock=clock,
    )
    bot.start()
    return bot, state


def test_4_stuck_detection_sets_alarm_not_exception() -> None:
    clock = Clock()
    bot, _ = make_cavebot(clock, [Waypoint(150, 150, 7)], (100, 100), 7)
    bot.step()
    assert bot.alarm is None
    clock.now = 5.1  # no movement for > 5 s
    bot.step()  # must not raise
    assert bot.alarm == "stuck"
    assert not bot.is_active()


def test_5_lost_floor_detection_sets_alarm() -> None:
    clock = Clock()
    bot, state = make_cavebot(clock, [Waypoint(150, 150, 7)], (100, 100), 7)
    bot.step()
    state.position = Position(100, 100, 9)  # unexpected floor change; no waypoint on floor 9
    clock.now = 1.0
    bot.step()
    assert bot.alarm == "lost"
    assert not bot.is_active()


def test_6_release_all_on_normal_stop() -> None:
    ws = make_state()
    inp = FakeInput()
    gl, _ = make_loop(ws, inp)
    gl.stop("test")
    gl.run()
    # A bare stop() returns without input; the exit path used by the UI/CLI
    # is close(), which is the safety release.
    inp.close()
    assert inp.sent_lines == ["X"]
    ws2 = make_state()
    inp2 = FakeInput()
    gl2, _ = make_loop(ws2, inp2)
    gl2.kill()
    gl2.tick()
    assert inp2.sent_lines == ["X"]


def test_7_hp_emergency_threshold_triggers_emergency_heal() -> None:
    healing = HealingDecision(
        HealingConfig(), spell_combos={"exura gran": "F1", "exura": "F2"}
    )
    low = healing.decide(make_state(hp_percent=25.0))
    assert low is not None and low.type is HealType.EMERGENCY
    mid = healing.decide(make_state(hp_percent=60.0))
    assert mid is not None and mid.type is HealType.NORMAL
    # through the loop: the emergency spell combo goes out
    inp = FakeInput()
    gl, _ = make_loop(make_state(hp_percent=25.0), inp, healing=healing)
    gl.tick()
    assert gl.current_state is BotState.EMERGENCY_HEAL
    assert inp.sent_lines == ["K F1"]


def test_8_low_mp_stops_casting_spells() -> None:
    healing = HealingDecision(HealingConfig(), spell_combos={"exura gran": "F1", "exura": "F2"})
    assert healing.decide(make_state(hp_percent=10.0, mp_percent=5.0)) is None
    inp = FakeInput()
    gl, _ = make_loop(make_state(hp_percent=10.0, mp_percent=5.0), inp, healing=healing)
    gl.tick()
    assert inp.sent_lines == []  # no spell cast


def test_9_rate_limit_never_exceeds_20hz() -> None:
    ws = make_state(hp_percent=50.0)  # a heal is wanted on every tick
    inp = FakeInput()
    gl, clock = make_loop(ws, inp)
    start = clock.now
    while clock.now - start < 1.0:
        gl.tick()
        clock.sleep(1.0 / 20.0)
    stats = gl.get_stats()
    assert cast(int, stats["tick_count"]) <= 21  # 20 Hz (+ the t=0 tick)
    assert len(inp.sent_lines) <= 12  # and input is capped at 10/s (+2 burst)
    assert cast(int, stats["actions_dropped_rate_limit"]) > 0


def test_10_no_input_while_idle() -> None:
    inp = FakeInput()
    gl, _ = make_loop(make_state(), inp)
    for _ in range(20):
        gl.tick()
    assert gl.current_state is BotState.IDLE
    assert inp.sent_lines == []


def test_11_loot_window_input_only_when_open_and_focused() -> None:
    inp = FakeInput()
    ws = make_state()
    reader = mock.Mock()
    cfg = LootingConfig(loot_hotkey="F7", move_all_hotkey="F6")
    looting = Looting(
        reader, mock.Mock(), inp, ws, cfg, frame_provider=lambda: np.zeros((4, 4, 3), np.uint8)
    )
    item = mock.Mock()
    window = LootWindow(open=True, items=[item])

    # Open window but unfocused client -> nothing.
    looting.on_target_died("Wolf")
    reader.read_loot_window.return_value = window
    ws.client_focused = False
    assert looting.step(ws) is False
    assert inp.sent_lines == []

    # Focused + open window -> the items are moved.
    ws.client_focused = True
    looting.step(ws)
    assert inp.sent_lines == ["K F6"]

    # Window never opens: only the single open-hotkey press, then give up.
    inp2 = FakeInput()
    looting2 = Looting(
        reader, mock.Mock(), inp2, ws, cfg, frame_provider=lambda: np.zeros((4, 4, 3), np.uint8)
    )
    reader.read_loot_window.return_value = None
    looting2.on_target_died("Wolf")
    for _ in range(30):
        looting2.step(ws)
    assert inp2.sent_lines == ["K F7"]
    assert looting2.pending is None


def test_12_refill_only_starts_above_cap_threshold(tmp_path: Path) -> None:
    route = {
        "name": "refill",
        "waypoints": [
            {"x": 100, "y": 100, "z": 7},
            {"action": "label", "label": "storage"},
            {"x": 100, "y": 100, "z": 7},
            {"action": "label", "label": "farming"},
        ],
    }
    (tmp_path / "refill.json").write_text(json.dumps(route))
    cavebot = mock.Mock()
    cavebot.is_active.return_value = False
    cavebot.alarm = None
    looting = mock.Mock()
    looting.pending = None
    refill = Refill(
        RefillConfig(trigger_cap=90.0, routes_dir=tmp_path),
        cavebot,
        RouteFileLoader(),
        looting,
        make_state(),
        FakeInput(),
    )
    below = make_state(capacity=Capacity(weight=50.0, max_weight=100.0))
    above = make_state(capacity=Capacity(weight=95.0, max_weight=100.0))
    assert not refill.should_refill(below)
    assert refill.should_refill(above)

    # Through the loop: REFILL state only when cap is high.
    gl, _ = make_loop(below, FakeInput(), refill=refill, cavebot=cavebot)
    gl.tick()
    assert gl.current_state is BotState.IDLE
    gl, _ = make_loop(above, FakeInput(), refill=refill, cavebot=cavebot)
    gl.tick()
    assert gl.current_state is BotState.REFILL


# -- F-19: the production wiring (cli.build_loop + make_perceive + focus) ---------

GOLDEN_LOW_HP = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "golden"
    / "2026-07-05_231254331_Secco Cio_LowHealth_1.png"
)
USER_PROFILE = default_profiles_dir() / "moonlight_user.json"
needs_golden = pytest.mark.skipif(
    not GOLDEN_LOW_HP.is_file() or not USER_PROFILE.is_file(),
    reason="no golden frame or user profile",
)


class Built:
    """A loop built by ``cli.build_loop`` over a real golden frame (HP ~41%)."""

    def __init__(self, **frame_overrides: Any) -> None:
        bgr = cv2.imread(str(GOLDEN_LOW_HP))
        assert bgr is not None
        self.frame = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        self.focused = True
        self.frame_error = False
        self.inp = FakeInput()
        self.clock = Clock()
        self.alerts: list[str] = []
        config = replace(BotConfig.default(), loop=LoopConfig(enable_f12_monitor=False))
        self.gl = build_loop(
            config,
            CalibrationProfile.load(USER_PROFILE),
            self.inp,
            frame_provider=self._frame,
            focus_check=lambda: self.focused,
            clock=self.clock,
            sleep_fn=self.clock.sleep,
            alert=self.alerts.append,
        )

    def _frame(self) -> np.ndarray:
        if self.frame_error:
            raise OSError("capture failed")
        return self.frame


@needs_golden
def test_13_built_loop_heals_from_golden_frame() -> None:
    b = Built()
    b.gl.tick()
    assert b.gl.current_state is BotState.NORMAL_HEAL
    assert b.inp.sent_lines and "X" not in b.inp.sent_lines  # a real heal went out


@needs_golden
def test_14_built_kill_while_unfocused_releases() -> None:
    b = Built()
    b.focused = False
    b.gl.kill()
    b.gl.tick()
    assert b.gl.stop_requested
    assert b.inp.sent_lines == ["X"]  # release is not gated by focus


@needs_golden
def test_15_built_screen_not_ok_pauses_then_stop_releases() -> None:
    b = Built()
    b.frame_error = True  # perception fails -> screen_ok False
    b.gl.run()  # pauses, then stops after pause_timeout
    assert b.gl.stop_requested
    assert set(b.inp.sent_lines) == {"X"} and len(b.inp.sent_lines) >= 2
    assert len(b.alerts) == 1


@needs_golden
def test_16_built_module_exception_releases_and_stops() -> None:
    b = Built()
    healing = b.gl.modules.healing
    assert healing is not None
    with mock.patch.object(healing, "decide", side_effect=RuntimeError("boom")):
        b.gl.run()
    assert b.gl.stop_requested
    assert b.gl.get_stats()["tick_count"] == LoopConfig().max_consecutive_errors
    assert set(b.inp.sent_lines) == {"X"}  # released, never acted
    assert len(b.alerts) == 1


@needs_golden
def test_17_built_perceive_exception_releases_and_stops() -> None:
    b = Built()
    with mock.patch.object(b.gl, "_perceive", side_effect=RuntimeError("boom")):
        b.gl.run()
    assert b.gl.stop_requested
    assert set(b.inp.sent_lines) == {"X"}


@needs_golden
def test_18_built_pause_on_unfocus_resume_on_refocus() -> None:
    b = Built()
    b.gl.tick()
    heals = len(b.inp.sent_lines)
    assert heals >= 1

    b.focused = False
    for _ in range(10):
        b.clock.sleep(0.05)
        b.gl.tick()
    assert b.gl.current_state is BotState.PAUSED
    assert b.inp.sent_lines[heals:] == ["X"]  # one release, then silence

    b.focused = True
    b.clock.sleep(5.0)  # past any heal cooldown
    b.gl.tick()
    assert b.gl.get_stats()["state"] == BotState.NORMAL_HEAL.value
    assert not b.gl.stop_requested
    assert len(b.inp.sent_lines) > heals + 1 and b.inp.sent_lines[-1] != "X"
