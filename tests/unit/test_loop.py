"""Unit tests for moonlight.loop (GameLoop, RateLimiter, F12Monitor).

Fully hermetic: no hardware, no Windows, no real sleeps on the game
paths. Timing uses an injectable fake clock; F12 uses an injectable
key-state probe (that suite uses tiny real sleeps for the thread).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest
from moonlight.input import FakeInput
from moonlight.loop import (
    VK_F12,
    BotState,
    F12Monitor,
    GameLoop,
    LoopConfig,
    RateLimiter,
)
from moonlight.state import Monster, WorldState

PERIOD = 1.0 / 20.0  # 50 ms


class FakeClock:
    """Deterministic monotonic clock with a manual sleep."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt

    def sleep(self, dt: float) -> None:
        self.sleeps.append(dt)
        self.now += max(0.0, dt)


class KeySim:
    """Controllable fake keyboard for F12Monitor tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._down: set[int] = set()
        self.samples = 0

    def down(self, vk: int) -> None:
        with self._lock:
            self._down.add(vk)

    def up(self, vk: int) -> None:
        with self._lock:
            self._down.discard(vk)

    def __call__(self, vk: int) -> bool:
        with self._lock:
            self.samples += 1
            return vk in self._down


def wait_until(pred: Callable[[], bool], timeout: float = 2.0) -> bool:
    """Poll *pred* until true or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.002)
    return pred()


def make_state(
    *,
    screen_ok: bool = True,
    focused: bool = True,
    hp: float = 100.0,
    players: int = 0,
    target: bool = False,
) -> WorldState:
    ws = WorldState()
    with ws.lock():
        ws.screen_ok = screen_ok
        ws.client_focused = focused
        ws.hp_percent = hp
        ws.players_on_screen = players
        ws.selected_target = Monster(name="Wolf") if target else None
    return ws


def make_loop(
    ws: WorldState,
    inp: FakeInput,
    *,
    clock: FakeClock | None = None,
    config: LoopConfig | None = None,
    **extra: object,
) -> tuple[GameLoop, FakeClock]:
    c = clock if clock is not None else FakeClock()
    cfg = config if config is not None else LoopConfig(enable_f12_monitor=False)
    gl = GameLoop(ws, inp, cfg, clock=c, sleep_fn=c.sleep, **extra)  # type: ignore[arg-type]
    return gl, c


class TestBotStatePriority:
    def test_declaration_order_is_priority(self) -> None:
        expected = [
            BotState.KILL,
            BotState.ALARM,
            BotState.EMERGENCY_HEAL,
            BotState.NORMAL_HEAL,
            BotState.ATTACK,
            BotState.MOVE,
            BotState.IDLE,
        ]
        assert [BotState(s) for s in expected] == list(BotState)

    def test_priority_ranks(self) -> None:
        assert BotState.KILL.priority == 0
        assert BotState.IDLE.priority == 6
        assert BotState.ALARM.priority < BotState.EMERGENCY_HEAL.priority


class TestRateLimiter:
    def test_initial_burst_limited_to_capacity(self) -> None:
        rl = RateLimiter(rate=10.0, capacity=3.0)
        assert all(rl.try_acquire() for _ in range(3))
        assert not rl.try_acquire()

    def test_refills_at_rate(self) -> None:
        c = FakeClock()
        rl = RateLimiter(rate=10.0, capacity=1.0, clock=c)
        assert rl.try_acquire()
        assert not rl.try_acquire()
        c.advance(0.1)  # one token at 10/s
        assert rl.try_acquire()
        assert not rl.try_acquire()

    def test_tokens_capped_at_capacity(self) -> None:
        c = FakeClock()
        rl = RateLimiter(rate=10.0, capacity=2.0, clock=c)
        c.advance(10.0)  # would add 100 tokens
        assert rl.try_acquire()
        assert rl.try_acquire()
        assert not rl.try_acquire()

    def test_invalid_rate(self) -> None:
        with pytest.raises(ValueError):
            RateLimiter(rate=0.0)
        with pytest.raises(ValueError):
            RateLimiter(rate=-1.0)


class TestF12Monitor:
    def test_rising_edge_triggers_on_press(self) -> None:
        presses: list[int] = []
        key = KeySim()
        mon = F12Monitor(on_press=lambda: presses.append(1), poll_interval=0.002, key_state=key)
        mon.start()
        try:
            # Wait until the monitor has taken at least one sample (with the
            # key up), so prev_down is reliably initialized to False.
            assert wait_until(lambda: key.samples >= 1)
            key.down(VK_F12)
            assert wait_until(lambda: len(presses) == 1)
            # A held key must not retrigger.
            time.sleep(0.05)
            assert len(presses) == 1
            key.up(VK_F12)
            time.sleep(0.05)
            key.down(VK_F12)
            assert wait_until(lambda: len(presses) == 2)
        finally:
            mon.stop()
        assert not mon.running

    def test_no_press_without_key(self) -> None:
        presses: list[int] = []
        key = KeySim()
        mon = F12Monitor(on_press=lambda: presses.append(1), poll_interval=0.002, key_state=key)
        mon.start()
        try:
            time.sleep(0.05)
            assert presses == []
        finally:
            mon.stop()

    def test_invalid_poll_interval(self) -> None:
        with pytest.raises(ValueError):
            F12Monitor(on_press=lambda: None, poll_interval=0.0)


class TestGameLoopSafety:
    def test_no_input_when_not_focused(self) -> None:
        ws = make_state(focused=False, hp=20.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert gl.current_state is BotState.EMERGENCY_HEAL
        assert inp.sent_lines == []
        stats = gl.get_stats()
        assert stats["actions_dropped_unfocused"] == 1

    def test_no_input_when_screen_not_ok(self) -> None:
        ws = make_state(screen_ok=False, hp=20.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert inp.sent_lines == []
        assert gl.stop_requested

    def test_kill_releases_all_and_stops(self) -> None:
        ws = make_state(hp=80.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.kill()
        assert gl.kill_switch_active
        gl.tick()
        assert gl.current_state is BotState.KILL
        assert "X" in inp.sent_lines  # release_all
        assert gl.stop_requested

    def test_kill_release_respects_focus_gate(self) -> None:
        ws = make_state(focused=False, hp=80.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.kill()
        gl.tick()
        # Never send input unless focused -- even on the kill path.
        assert inp.sent_lines == []
        assert gl.current_state is BotState.KILL
        assert gl.stop_requested

    def test_f12_monitor_triggers_kill(self) -> None:
        ws = make_state(hp=80.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        key = KeySim()
        mon = F12Monitor(on_press=gl.kill, poll_interval=0.002, key_state=key)
        mon.start()
        try:
            assert wait_until(lambda: key.samples >= 1)
            key.down(VK_F12)
            assert wait_until(lambda: gl.kill_switch_active)
        finally:
            mon.stop()


class TestGameLoopStates:
    def test_emergency_heal(self) -> None:
        ws = make_state(hp=20.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert gl.current_state is BotState.EMERGENCY_HEAL
        assert "K 1" in inp.sent_lines

    def test_normal_heal(self) -> None:
        ws = make_state(hp=50.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert gl.current_state is BotState.NORMAL_HEAL
        assert "K 2" in inp.sent_lines

    def test_attack_when_target_selected(self) -> None:
        ws = make_state(hp=100.0, target=True)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert gl.current_state is BotState.ATTACK
        assert "K r" in inp.sent_lines

    def test_move_when_route_active(self) -> None:
        ws = make_state(hp=100.0)
        inp = FakeInput()
        steps: list[int] = []
        gl, _ = make_loop(
            ws,
            inp,
            move_provider=lambda s: True,
            move_action=lambda: steps.append(1),
        )
        gl.tick()
        assert gl.current_state is BotState.MOVE
        assert steps == [1]

    def test_idle_when_nothing_to_do(self) -> None:
        ws = make_state(hp=100.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert gl.current_state is BotState.IDLE
        assert inp.sent_lines == []

    def test_unknown_hp_is_fail_closed(self) -> None:
        # hp_percent 0.0 means "unknown": never heal on unknown vitals.
        ws = make_state(hp=0.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert gl.current_state is BotState.IDLE
        assert inp.sent_lines == []

    def test_alarm_on_players_on_screen(self) -> None:
        ws = make_state(players=2, target=True, hp=100.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert gl.current_state is BotState.ALARM
        assert "K r" not in inp.sent_lines  # no attack while alarming

    def test_alarm_keeps_healing(self) -> None:
        ws = make_state(players=1, hp=20.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert gl.current_state is BotState.ALARM
        assert "K 1" in inp.sent_lines  # emergency heal continues

    def test_alarm_on_fast_hp_drop(self) -> None:
        ws = make_state(hp=100.0)
        inp = FakeInput()
        gl, clock = make_loop(ws, inp)
        gl.tick()  # t=0: HP 100 recorded
        with ws.lock():
            ws.hp_percent = 50.0
        clock.advance(2.5)  # beyond the 2.0s alarm window
        gl.tick()  # 50% drop within window -> ALARM
        assert gl.current_state is BotState.ALARM

    def test_priority_alarm_over_heal_over_attack(self) -> None:
        ws = make_state(players=1, hp=10.0, target=True)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert gl.current_state is BotState.ALARM  # not EMERGENCY_HEAL

        # Heal outranks attack.
        ws2 = make_state(hp=10.0, target=True)
        inp2 = FakeInput()
        gl2, _ = make_loop(ws2, inp2)
        gl2.tick()
        assert gl2.current_state is BotState.EMERGENCY_HEAL  # not ATTACK
        assert "K r" not in inp2.sent_lines

    def test_priority_heal_over_move(self) -> None:
        ws = make_state(hp=50.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp, move_provider=lambda s: True)
        gl.tick()
        assert gl.current_state is BotState.NORMAL_HEAL  # not MOVE


class TestGameLoopRateLimit:
    def test_actions_capped_at_10_per_second(self) -> None:
        ws = make_state(hp=100.0, target=True)
        inp = FakeInput()
        gl, clock = make_loop(ws, inp)
        # 20 ticks with no time passing: the bucket (capacity 1) allows
        # only the first action; every other attempt is rate-limited.
        for _ in range(20):
            gl.tick()
        stats = gl.get_stats()
        assert stats["actions_sent"] == 1
        assert stats["actions_dropped_rate_limit"] == 19

        # Advance 0.1s (one token at 10/s): exactly one more action.
        clock.advance(0.1)
        gl.tick()
        assert gl.get_stats()["actions_sent"] == 2


class TestGameLoopRun:
    def test_run_stops_when_screen_not_ok(self) -> None:
        ws = make_state(screen_ok=False)
        inp = FakeInput()
        gl, clock = make_loop(ws, inp)
        gl.run()
        assert gl.stop_requested
        assert gl.get_stats()["tick_count"] == 1
        assert inp.sent_lines == []
        assert clock.sleeps == []

    def test_run_returns_immediately_when_pre_stopped(self) -> None:
        ws = make_state(hp=100.0)
        inp = FakeInput()
        gl, clock = make_loop(ws, inp)
        gl.stop("test")
        gl.run()
        assert gl.get_stats()["tick_count"] == 0
        assert clock.sleeps == []

    def test_run_returns_immediately_when_pre_killed(self) -> None:
        ws = make_state(hp=100.0)
        inp = FakeInput()
        gl, clock = make_loop(ws, inp)
        gl.kill()
        gl.run()
        assert gl.kill_switch_active
        assert gl.get_stats()["tick_count"] == 0
        assert clock.sleeps == []

    def test_run_ticks_at_20hz_until_kill(self) -> None:
        ws = make_state(hp=100.0)
        inp = FakeInput()
        calls = {"n": 0}

        def move_action() -> None:
            calls["n"] += 1
            if calls["n"] == 3:
                gl.kill()  # kill mid-run from a "device" side effect

        gl, clock = make_loop(
            ws,
            inp,
            move_provider=lambda s: True,
            move_action=move_action,
        )
        gl.run()

        # Ticks at t=0, .05, .10, .15: the kill requested on tick 3 is
        # processed on tick 4 (KILL), which stops the loop.
        assert gl.get_stats()["tick_count"] == 4
        assert gl.current_state is BotState.KILL
        assert gl.kill_switch_active
        assert gl.stop_requested
        assert "X" in inp.sent_lines  # kill released everything
        # Four sleep calls of 50 ms each (20 Hz pacing).
        assert len(clock.sleeps) == 3
        assert all(abs(s - PERIOD) < 1e-9 for s in clock.sleeps)


class TestGameLoopStats:
    def test_stats_shape_and_counts(self) -> None:
        ws = make_state(hp=10.0, target=True)
        inp = FakeInput()
        gl, clock = make_loop(ws, inp)
        gl.tick()
        clock.advance(0.05)
        gl.tick()  # rate-limited: only 1 action in 0.05s
        stats = gl.get_stats()
        assert stats["state"] == "emergency_heal"
        assert stats["tick_count"] == 2
        assert stats["actions_sent"] == 1
        assert stats["actions_dropped_rate_limit"] == 1
        assert stats["kill_switch_active"] is False
        assert stats["stop_requested"] is False
        assert isinstance(stats["state_transitions"], dict)
        assert isinstance(stats["avg_tick_time_ms"], float)

    def test_reset_stats(self) -> None:
        ws = make_state(hp=10.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        gl.reset_stats()
        assert gl.get_stats()["tick_count"] == 0
        assert gl.get_stats()["actions_sent"] == 0
