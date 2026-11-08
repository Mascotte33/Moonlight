"""Unit tests for moonlight.core.loop (GameLoop).

Fully hermetic: no hardware, no Windows, no real sleeps on the game
paths. Timing uses an injectable fake clock; F12 uses an injectable
key-state probe (that suite uses tiny real sleeps for the thread).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import numpy as np
import pytest

from moonlight.alarms.alarms import AlarmDecision
from moonlight.cavebot.cavebot import Cavebot
from moonlight.core.loop import BotState, DecisionModules, GameLoop, LoopConfig
from moonlight.core.safety import VK_F12, F12Monitor, RateLimiter
from moonlight.core.state import Monster, WorldState
from moonlight.extras.extras import Extras, ExtrasConfig
from moonlight.hardware.clicker import ClickController
from moonlight.hardware.input import ClickSide, FakeInput, InputBackend
from moonlight.healing.healing import HealingAction, HealingDecision
from moonlight.looting.looting import Looting
from moonlight.scripts.rotation import Account, Rotation, RotationConfig
from moonlight.scripts.scripts import ScriptRunner
from moonlight.targeting.attack_hotkeys import AttackHotkeys
from moonlight.targeting.targeting import AreaRule, MonsterRow, TargetingConfig, TargetingDecision

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
    # Default healing rules: F2 spell at HP 50, F1 at 85, F3 potion at HP 40.
    extra.setdefault("modules", DecisionModules(healing=HealingDecision(clock=c)))
    gl = GameLoop(ws, inp, cfg, clock=c, sleep_fn=c.sleep, **extra)  # type: ignore[arg-type]
    return gl, c


class TestBotStatePriority:
    def test_declaration_order_is_priority(self) -> None:
        expected = [
            BotState.KILL,
            BotState.PAUSED,
            BotState.ATTACK,
            BotState.LOOT,
            BotState.MOVE,
            BotState.IDLE,
        ]
        assert [BotState(s) for s in expected] == list(BotState)

    def test_priority_ranks(self) -> None:
        assert BotState.KILL.priority == 0
        assert BotState.PAUSED.priority == 1
        assert BotState.IDLE.priority == 5


class TestGameLoopSafety:
    def test_no_input_when_not_focused(self) -> None:
        ws = make_state(focused=False, hp=20.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert inp.sent_lines == []
        stats = gl.get_stats()
        assert stats["actions_dropped_unfocused"] == 2  # spell and potion

    def test_no_input_when_screen_not_ok(self) -> None:
        ws = make_state(screen_ok=False, hp=20.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert gl.current_state is BotState.PAUSED
        assert inp.sent_lines == ["X"]  # only the release on entering PAUSED
        assert not gl.stop_requested

    def test_pause_resumes_when_screen_ok_again(self) -> None:
        ws = make_state(screen_ok=False, hp=100.0, target=True)
        inp = FakeInput()
        gl, clock = make_loop(ws, inp)
        for _ in range(3):
            gl.tick()
            clock.advance(PERIOD)
        assert gl.current_state is BotState.PAUSED
        assert inp.sent_lines == ["X"]  # released once, nothing else
        with ws.lock():
            ws.screen_ok = True
        gl.tick()
        assert gl.get_stats()["state"] == BotState.ATTACK.value
        assert inp.sent_lines == ["X"]  # the client attacks the selection: no key

    # doc: docs/prs/pause-stop.md#pause-keeps-running
    def test_a_long_pause_alerts_once_and_keeps_waiting(self) -> None:
        ws = make_state(screen_ok=False)
        alerts: list[str] = []
        gl, clock = make_loop(ws, FakeInput(), alert=alerts.append)
        gl.tick()
        clock.advance(LoopConfig().pause_timeout - 1.0)
        gl.tick()
        assert alerts == []
        for _ in range(3):
            clock.advance(2.0)
            gl.tick()
        assert not gl.stop_requested  # the loop (and the Arduino) stays up
        assert len(alerts) == 1 and "still waiting" in alerts[0]
        with ws.lock():
            ws.screen_ok = True
        gl.tick()
        assert gl.current_state is not BotState.PAUSED
        with ws.lock():
            ws.screen_ok = False
        gl.tick()
        clock.advance(LoopConfig().pause_timeout + 1.0)
        gl.tick()
        assert len(alerts) == 2  # a new long pause alerts again

    # doc: docs/prs/pause-stop.md#stop-reads-only
    def test_stopped_reads_but_presses_nothing(self) -> None:
        ws = make_state(hp=10.0, screen_ok=False)
        inp = FakeInput()
        alerts: list[str] = []
        seen: list[int] = []
        gl, clock = make_loop(ws, inp, alert=alerts.append, perceive=lambda: seen.append(1))
        gl.stopped = True
        for _ in range(int(LoopConfig().pause_timeout) * 2):
            gl.tick()
            clock.advance(1.0)
        assert inp.sent_lines == ["X"]  # released once, no heal at 10 % HP
        assert len(seen) == int(LoopConfig().pause_timeout) * 2 // 4  # read every 4th tick
        assert alerts == [] and not gl.stop_requested
        assert gl.get_stats()["current_task"] == "Stopped"
        gl.stopped = False
        with ws.lock():
            ws.screen_ok = True
        gl.tick()
        assert "K F2" in inp.sent_lines  # Start: healing again

    def test_run_tick_exception_releases_pauses_then_stops(self) -> None:
        ws = make_state(hp=100.0)
        inp = FakeInput()
        alerts: list[str] = []

        def boom() -> None:
            raise RuntimeError("device lost")

        gl, _ = make_loop(
            ws, inp, move_provider=lambda s: True, move_action=boom, alert=alerts.append
        )
        gl.run()  # must return, not raise
        assert gl.stop_requested
        assert gl.get_stats()["tick_count"] == LoopConfig().max_consecutive_errors
        assert inp.sent_lines[0] == "X"  # released on the first exception
        assert inp.sent_lines[-1] == "X"  # and on shutdown
        assert len(alerts) == 1

    def test_release_failure_is_swallowed(self) -> None:
        ws = make_state(focused=False)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        inp.release_all = lambda: (_ for _ in ()).throw(RuntimeError("dead"))  # type: ignore[method-assign]
        gl.kill()
        gl.tick()  # must not raise
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

    def test_kill_releases_even_when_unfocused(self) -> None:
        ws = make_state(focused=False, screen_ok=False, hp=80.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.kill()
        gl.tick()
        # release_all is the one command that bypasses the focus gate.
        assert inp.sent_lines == ["X"]
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
    def test_low_hp_sends_spell_and_potion(self) -> None:
        ws = make_state(hp=20.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert inp.sent_lines == ["K F2", "K F3"]

    def test_mid_hp_sends_the_light_spell(self) -> None:
        ws = make_state(hp=80.0)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert inp.sent_lines == ["K F1"]

    def test_attack_when_target_selected(self) -> None:
        ws = make_state(hp=100.0, target=True)
        inp = FakeInput()
        gl, _ = make_loop(ws, inp)
        gl.tick()
        assert gl.current_state is BotState.ATTACK
        assert inp.sent_lines == []  # never the attack key

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

    def test_pause_drops_cavebot_and_targeting_keeps_healing(self) -> None:
        ws = make_state(hp=50.0)
        steps: list[int] = []
        released: list[int] = []
        cavebot = SimpleNamespace(
            is_active=lambda: True,
            step=lambda: steps.append(1),
            resume=lambda after_fight=False: None,
            luring=lambda: False,
            route=SimpleNamespace(current=0),
            release_walk_key=lambda: released.append(1),
        )
        inp = FakeInput()
        clock = FakeClock()
        modules = DecisionModules(
            cavebot=cast(Cavebot, cavebot), healing=HealingDecision(clock=clock)
        )
        gl, _ = make_loop(ws, inp, clock=clock, modules=modules)
        gl.all_modules.targeting = cast(TargetingDecision, object())
        gl.paused = True
        assert gl.modules.cavebot is None and gl.modules.targeting is None
        gl.all_modules.targeting = None
        gl.tick()
        assert inp.sent_lines == ["K F2"]
        ws.hp_percent = 100.0
        gl.tick()
        assert steps == []
        assert released  # a walk key held when Pause was pressed is let go
        gl.paused = False
        clock.sleep(1.0)  # let the rate limiter refill after the heal
        gl.tick()
        assert steps == [1]

    def test_replacing_the_cavebot_lets_go_of_its_walk_key(self) -> None:
        released: list[int] = []
        cavebot = SimpleNamespace(release_walk_key=lambda: released.append(1))
        gl, _ = make_loop(
            make_state(), FakeInput(), modules=DecisionModules(cavebot=cast(Cavebot, cavebot))
        )
        gl.modules = DecisionModules(cavebot=gl.all_modules.cavebot)
        assert released == []
        gl.modules = DecisionModules()  # the cavebot switched off
        assert released == [1]

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

    # doc: docs/prs/death-stop.md#death-stop
    def test_empty_hp_bar_for_three_seconds_stops_the_bot(self) -> None:
        ws = make_state(hp=50.0)
        alerts: list[str] = []
        gl, clock = make_loop(ws, FakeInput(), alert=alerts.append)
        gl.screen_grab = lambda: np.zeros((4, 4, 3), np.uint8)
        gl.tick()
        ws.hp_percent = 0.0
        gl.tick()
        clock.advance(2.9)
        gl.tick()
        assert not gl.stop_requested  # a living character is healed back in time
        clock.advance(0.1)
        with patch("moonlight.extras.extras.save_screenshot") as save:
            gl.tick()
        assert gl.stop_requested and gl.died and save.called
        assert alerts == ["loop stopping: character died (HP bar empty for 3 s)"]

    def test_a_death_is_reported_while_stopped(self) -> None:
        ws = make_state(hp=50.0)
        gl, clock = make_loop(ws, FakeInput())
        gl.stopped = True
        for hp in (50.0, 0.0, 0.0, 0.0):
            ws.hp_percent = hp
            for _ in range(4):  # stopped reads every 4th tick
                gl.tick()
            clock.advance(1.5)
        assert gl.stop_requested

    def test_hp_never_read_is_no_death(self) -> None:
        ws = make_state(hp=0.0)
        gl, clock = make_loop(ws, FakeInput())
        for _ in range(5):
            gl.tick()
            clock.advance(1.0)
        assert not gl.stop_requested

    def test_priority_heal_over_attack(self) -> None:
        ws2 = make_state(hp=10.0, target=True)
        inp2 = FakeInput()
        gl2, _ = make_loop(ws2, inp2)
        gl2.tick()
        assert inp2.sent_lines == ["K F2", "K F3"]

    # doc: docs/prs/extras.md#alarms
    def test_low_hp_alarm_alerts_once_and_the_bot_keeps_attacking(self) -> None:
        ws = make_state(hp=10.0, target=True)
        clock = FakeClock()
        alerts: list[str] = []
        modules = DecisionModules(alarms=AlarmDecision(clock=clock))
        gl, _ = make_loop(ws, FakeInput(), clock=clock, modules=modules, alert=alerts.append)
        gl.tick()
        clock.advance(0.1)
        gl.tick()
        assert alerts == ["ALARM: HP below 20%"]
        assert gl.current_state is BotState.ATTACK

    def test_priority_heal_over_move(self) -> None:
        ws = make_state(hp=50.0)
        inp = FakeInput()
        steps: list[int] = []
        gl, _ = make_loop(
            ws, inp, move_provider=lambda s: True, move_action=lambda: steps.append(1)
        )
        gl.tick()
        assert inp.sent_lines == ["K F2"]
        assert steps == []  # the heal skipped the step


    # doc: docs/prs/scripts.md#script-priority
    def test_script_pauses_walking_never_healing(self, tmp_path: Path) -> None:
        (tmp_path / "s.py").write_text("def run(bot):\n    yield bot.press('f9')\n")
        ws = make_state(hp=50.0)
        inp = FakeInput()
        steps: list[int] = []
        c = FakeClock()
        scripts = ScriptRunner(tmp_path, timers=[("s", 60.0)], clock=c)
        modules = DecisionModules(healing=HealingDecision(clock=c), scripts=scripts)
        gl, _ = make_loop(
            ws, inp, clock=c, modules=modules,
            move_provider=lambda s: True, move_action=lambda: steps.append(1),
        )
        gl.tick()
        assert inp.sent_lines == ["K F2"]  # the heal first
        with ws.lock():
            ws.hp_percent = 100.0
        for _ in range(3):
            c.advance(1.0)
            gl.tick()
        assert inp.sent_lines == ["K F2", "K f9"] and steps == []
        c.advance(1.0)
        gl.tick()
        assert steps == [1]  # the script ended: walking again

    # doc: docs/prs/scripts-page.md#test-now
    def test_pause_holds_ticked_scripts_but_runs_test_now(self, tmp_path: Path) -> None:
        (tmp_path / "s.py").write_text("def run(bot):\n    yield bot.press('f9')\n")
        (tmp_path / "t.py").write_text("def run(bot):\n    yield bot.press('f8')\n")
        c = FakeClock()
        scripts = ScriptRunner(tmp_path, timers=[("s", 60.0)], clock=c)
        inp = FakeInput()
        gl, _ = make_loop(make_state(), inp, clock=c, modules=DecisionModules(scripts=scripts))
        gl.paused = True
        for _ in range(3):
            c.advance(1.0)
            gl.tick()
        assert inp.sent_lines == []  # paused: the ticked script waits
        scripts.request_test("t")
        for _ in range(3):
            c.advance(1.0)
            gl.tick()
        assert inp.sent_lines == ["K f8"]


class TestGameLoopRateLimit:
    def test_actions_capped_at_10_per_second(self) -> None:
        ws = make_state(hp=50.0)
        inp = FakeInput()
        # A healer with no cooldown: a heal is wanted on every tick.
        always_heal = SimpleNamespace(
            decide=lambda s: [HealingAction("spell", "F2", "test")],
            send=lambda action, backend: backend.press_combo(action.hotkey),
        )
        modules = DecisionModules(healing=cast(HealingDecision, always_heal))
        gl, clock = make_loop(ws, inp, modules=modules)
        # 20 ticks with no time passing: the bucket (capacity 3) allows
        # a 3-heal burst; every other attempt is rate-limited.
        for _ in range(20):
            gl.tick()
        stats = gl.get_stats()
        assert stats["actions_sent"] == 3
        assert stats["actions_dropped_rate_limit"] == 17

        # Advance 0.1s (one token at 10/s): exactly one more action.
        clock.advance(0.1)
        gl.tick()
        assert gl.get_stats()["actions_sent"] == 4

    def test_heal_never_waits_behind_attack(self) -> None:
        ws = make_state(hp=100.0)
        with ws.lock():
            ws.monsters = [Monster("Wolf")]
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp)
        gl.tick()  # the select click's jump takes a token, one stays reserved
        with ws.lock():
            ws.hp_percent = 50.0
        clock.advance(PERIOD)
        gl.tick()
        assert inp.sent_lines == ["A 189 222", "K F2"]

    def test_non_heal_actions_still_paced_at_10_per_second(self) -> None:
        rl = RateLimiter(rate=10.0, capacity=2.0, clock=lambda: 0.0)
        assert rl.try_acquire(reserve=1.0)
        assert not rl.try_acquire(reserve=1.0)  # the last token is reserved
        assert rl.try_acquire()  # ...for a heal


class FakeClicker:
    """Absolute-mode clicker double: one step jumps, the next one clicks."""

    def __init__(self) -> None:
        self.wire: InputBackend | None = None
        self.target: tuple[int, int] | None = None
        self.side = ClickSide.LEFT
        self.jumped = False
        #: Set, every click is followed by a jump here (the real one parks off
        #: the minimap and the battle list).
        self.park_point: tuple[int, int] | None = None
        self.parking = False

    @property
    def busy(self) -> bool:
        return self.target is not None or self.parking

    def request(self, target: tuple[int, int], side: ClickSide = ClickSide.LEFT) -> None:
        self.target, self.side, self.jumped = target, side, False

    def cancel(self) -> None:
        self.target, self.parking = None, False

    def step(self) -> bool | None:
        assert self.wire is not None
        if self.target is None:
            if self.parking and self.park_point is not None:
                self.wire.mouse_to(*self.park_point)
                self.parking = False
            return None
        if not self.jumped:
            self.wire.mouse_to(*self.target)
            self.jumped = True
            return None
        self.wire.click(self.side)
        self.target, self.parking = None, self.park_point is not None
        return True


def make_targeting_loop(
    ws: WorldState,
    inp: FakeInput,
    targeting_config: TargetingConfig | None = None,
    cavebot: object | None = None,
    toggle_chase_hotkey: str = "F9",
) -> tuple[GameLoop, FakeClock]:
    """A loop with real targeting over battle list region (100, 200, 170, 300).

    The game grid is (0, 0, 150, 110): 10 px tiles, slot (8, 5) centred at (85, 55).
    """
    clock = FakeClock()
    clicker = FakeClicker()
    config = targeting_config if targeting_config is not None else TargetingConfig()
    config = replace(config, toggle_chase_hotkey=toggle_chase_hotkey)
    modules = DecisionModules(
        healing=HealingDecision(clock=clock),
        targeting=TargetingDecision(
            config, (100, 200, 170, 300), game_grid_region=(0, 0, 150, 110)
        ),
        attack_hotkeys=AttackHotkeys(config),
        cavebot=cast(Cavebot, cavebot),
        clicker=cast(ClickController, clicker),
    )
    gl, _ = make_loop(ws, inp, clock=clock, modules=modules)
    clicker.wire = gl.input
    return gl, clock


def ticks(gl: GameLoop, clock: FakeClock, n: int) -> None:
    for _ in range(n):
        gl.tick()
        clock.advance(PERIOD)


#: The Chase Opponent key a follow row presses before its first attack key.


class TestScreenClick:
    """near-targeting: only creatures within N tiles, right-clicked on the game screen."""

    NEAR = TargetingConfig(
        only_attack_within_range=True,
        monsters=(MonsterRow(name="Rat", ignore=True),),
    )

    def near_state(self) -> WorldState:
        ws = make_state()
        with ws.lock():
            ws.monsters = [Monster("Wolf", 50.0)]
            ws.creature_slots, ws.creature_hp_percents = ((8, 5), (13, 5)), (50.0, 10.0)
        return ws

    def test_right_clicks_the_creature_in_range(self) -> None:
        inp = FakeInput()
        gl, clock = make_targeting_loop(self.near_state(), inp, self.NEAR)
        ticks(gl, clock, 3)
        assert gl.current_state is BotState.ATTACK
        assert inp.sent_lines == ["A 85 55", "C R"]  # the far (13, 5) one is never clicked

    def test_the_click_waits_until_the_player_stands(self) -> None:
        ws = self.near_state()
        with ws.lock():
            ws.game_view_sliding = True  # still finishing a step
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp, self.NEAR)
        ticks(gl, clock, 3)
        assert gl.current_state is BotState.ATTACK  # walking stopped at once
        assert inp.sent_lines == []
        with ws.lock():
            ws.game_view_sliding = False
        ticks(gl, clock, 3)
        assert inp.sent_lines == ["A 85 55", "C R"]

    def test_click_in_flight_holds_attack_when_the_creature_flickers(self) -> None:
        ws = self.near_state()
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp, self.NEAR)
        ticks(gl, clock, 1)  # the mouse jumps
        with ws.lock():
            ws.creature_slots, ws.creature_hp_percents = (), ()  # its bar is gone a frame
        ticks(gl, clock, 2)
        assert gl.current_state is BotState.ATTACK  # no walk takes the click over
        assert inp.sent_lines == ["A 85 55", "C R"]

    def test_nothing_selected_skips_the_tile(self, caplog: pytest.LogCaptureFixture) -> None:
        inp = FakeInput()
        gl, clock = make_targeting_loop(self.near_state(), inp, self.NEAR)
        with caplog.at_level(logging.INFO):
            ticks(gl, clock, 3 + 18 + 4 + 40)  # no outline after the click (a player)
        assert inp.sent_lines == ["A 85 55", "C R"]
        assert "nothing selected at 1,0,0, skipping that tile 30 s" in caplog.text

    def test_a_selected_ignored_monster_is_dropped_with_esc(self) -> None:
        ws = self.near_state()
        rat = Monster("Rat", 50.0, name_hash="r", battle_list_row=1)
        with ws.lock():
            ws.monsters.append(rat)
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp, self.NEAR)
        ticks(gl, clock, 3)
        with ws.lock():
            ws.selected_target = rat  # the click picked the never-attack Rat
        ticks(gl, clock, 20)
        assert inp.sent_lines == ["A 85 55", "C R", "K esc"]


class TestSelectClick:
    def test_clicks_the_name_centre_of_row_0(self) -> None:
        ws = make_state()
        with ws.lock():
            ws.monsters = [Monster("Wolf", 50.0)]
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp)
        ticks(gl, clock, 3)  # jump, rate-limited tick, click
        assert gl.current_state is BotState.ATTACK
        assert inp.sent_lines == ["A 189 222", "C L"]  # 100+23+66, 200+11+11

    def test_clicks_the_name_centre_of_row_3(self) -> None:
        ws = make_state()
        with ws.lock():
            ws.monsters = [Monster("Wolf", 90.0, battle_list_row=r) for r in range(3)]
            ws.monsters.append(Monster("Wolf", 10.0, battle_list_row=3))
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp)
        ticks(gl, clock, 3)
        assert inp.sent_lines == ["A 189 288", "C L"]  # 200+11+3*22+11

    # doc: docs/prs/near-targeting.md#moved-row
    def test_a_click_is_aimed_again_when_its_row_moves(self) -> None:
        ws = make_state()
        with ws.lock():
            ws.monsters = [Monster("Wolf", 50.0)]
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp)
        ticks(gl, clock, 1)  # the mouse jumps to row 0
        with ws.lock():
            ws.monsters = [Monster("Wolf", 50.0, battle_list_row=1)]  # the list reordered
        ticks(gl, clock, 4)
        assert inp.sent_lines == ["A 189 222", "A 189 244", "C L"]

    # doc: docs/prs/near-targeting.md#park-cursor
    def test_the_cursor_parks_after_the_click_once_the_target_is_chosen(self) -> None:
        ws = make_state()
        wolf = Monster("Wolf", 50.0)
        with ws.lock():
            ws.monsters = [wolf]
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp)
        cast(FakeClicker, gl.modules.clicker).park_point = (5, 5)
        ticks(gl, clock, 2)  # jump, click
        with ws.lock():
            ws.selected_target = wolf  # targeting now wants nothing more
        ticks(gl, clock, 3)
        assert inp.sent_lines == ["A 189 222", "C L", "A 5 5"]

    def test_one_click_in_flight_until_the_outline_shows(self) -> None:
        ws = make_state()
        wolf = Monster("Wolf", 50.0)
        with ws.lock():
            ws.monsters = [wolf]
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp)
        ticks(gl, clock, 3 + 18)  # sent at 0.10 s, no outline by 1.00 s
        assert inp.sent_lines == ["A 189 222", "C L"]
        ticks(gl, clock, 4)  # 1 s without the outline: click again
        assert inp.sent_lines == ["A 189 222", "C L"] * 2
        with ws.lock():
            ws.selected_target = wolf  # the outline shows
        ticks(gl, clock, 40)
        assert inp.sent_lines == ["A 189 222", "C L"] * 2  # no hotkey on the row
        assert gl.current_state is BotState.ATTACK

    def test_stuck_target_gets_one_arrow_step_then_the_next_target_is_clicked(self) -> None:
        ws = make_state()
        wolf, rat = Monster("Wolf", name_hash="w"), Monster("Rat", name_hash="r", battle_list_row=1)
        with ws.lock():
            ws.monsters = [wolf, rat]
            ws.selected_target = wolf
            ws.creature_slots = ((7, 2),)  # three tiles north
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp)
        ticks(gl, clock, 4 * 20 + 3)  # 4 s on a target whose HP never moves
        assert inp.sent_lines == ["K w"]
        ticks(gl, clock, 4 * 20 + 6)  # 4 s more, then the click on the next row
        assert inp.sent_lines == ["K w", "A 189 244", "C L"]


    def test_lone_stuck_target_pauses_targeting_with_a_countdown(self) -> None:
        ws = make_state()
        wolf = Monster("Wolf", name_hash="w")
        with ws.lock():
            ws.monsters = [wolf]
            ws.selected_target = wolf
        cavebot = SimpleNamespace(
            is_active=lambda: False,
            luring=lambda: False,
            route=SimpleNamespace(current=0),
            release_walk_key=lambda: None,
            alarm=None,
            targeting_paused=False,
        )
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp, cavebot=cavebot)
        ticks(gl, clock, 4 * 20 + 3)  # 4 s on a target whose HP never moves
        assert cavebot.targeting_paused and gl.current_state is not BotState.ATTACK
        assert "(targeting paused 5 s)" in str(gl.get_stats()["current_task"])
        ticks(gl, clock, 5 * 20 + 2)
        assert not cavebot.targeting_paused
        assert "targeting paused" not in str(gl.get_stats()["current_task"])
        assert inp.sent_lines == []

    def test_stuck_cavebot_click_on_the_creature_in_the_way_comes_before_row_0(self) -> None:
        ws = make_state()
        with ws.lock():
            ws.monsters = [Monster("Bat", name_hash="b")]  # row 0, not in the way
        cavebot = SimpleNamespace(
            is_active=lambda: True,
            step=lambda: None,
            luring=lambda: False,
            route=SimpleNamespace(current=0),
            release_walk_key=lambda: None,
            alarm=None,
            targeting_paused=False,
            stuck_on_creatures=True,
            creature_in_the_way_clicked_until=1.5,  # it right-clicked the rat in the way
            resume=lambda after_fight=False: None,
            refill_now=False,
        )
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp, cavebot=cavebot)
        gl.all_modules.stuck_targeting = gl.all_modules.targeting
        ticks(gl, clock, 20)  # 1 s: no row-0 click while that click may still select
        assert inp.sent_lines == []
        ticks(gl, clock, 20)  # it selected nothing: row 0 after all
        assert inp.sent_lines[-2:] == ["A 189 222", "C L"]


F6_ROWS = TargetingConfig(
    default_monster=MonsterRow(hotkey="F6"),
    area_rules=(AreaRule("F5", minimum_monsters=2),),
)


class TestAttackHotkeys:
    def _wolf_selected(self) -> WorldState:
        ws = make_state()
        wolf = Monster("Wolf", 50.0)
        with ws.lock():
            ws.monsters = [wolf]
            ws.selected_target = wolf
            ws.mp_percent = 80.0
        return ws

    def test_selected_target_gets_the_row_hotkey_then_the_cooldown(self) -> None:
        inp = FakeInput()
        gl, clock = make_targeting_loop(self._wolf_selected(), inp, F6_ROWS)
        ticks(gl, clock, 20)  # 1 s < the 2 s cooldown
        assert inp.sent_lines == ["K F6"]
        ticks(gl, clock, 25)
        assert inp.sent_lines == ["K F6", "K F6"]

    # doc: docs/prs/near-targeting.md#same-tick
    def test_a_heal_and_the_attack_go_out_on_one_tick(self) -> None:
        ws = self._wolf_selected()
        ws.hp_percent = 45.0  # the F2 spell only
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp, F6_ROWS)
        ticks(gl, clock, 1)
        assert inp.sent_lines == ["K F2", "K F6"]

    def test_after_spell_and_potion_the_attack_waits_for_a_token(self) -> None:
        ws = self._wolf_selected()
        ws.hp_percent = 30.0  # the F2 spell and the F3 potion
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp, F6_ROWS)
        ticks(gl, clock, 1)
        assert inp.sent_lines == ["K F2", "K F3"]  # both tokens spent on heals
        ws.hp_percent = 100.0
        ticks(gl, clock, 4)
        assert inp.sent_lines == ["K F2", "K F3", "K F6"]

    # doc: docs/prs/near-targeting.md#targeting-script
    def test_no_attack_while_a_script_turned_targeting_off(self, tmp_path: Path) -> None:
        inp = FakeInput()
        gl, clock = make_targeting_loop(self._wolf_selected(), inp, F6_ROWS)
        scripts = ScriptRunner(tmp_path, clock=clock)
        scripts.targeting_turned_off = True
        gl.modules = replace(gl.all_modules, scripts=scripts)
        ticks(gl, clock, 5)
        assert inp.sent_lines == [] and gl.modules.targeting is None
        scripts.targeting_turned_off = False
        ticks(gl, clock, 2)
        assert inp.sent_lines == ["K F6"]

    # doc: docs/prs/first-spell.md#fresh-cooldown
    def test_the_next_target_after_a_kill_is_attacked_at_once(self) -> None:
        inp = FakeInput()
        ws = self._wolf_selected()
        gl, clock = make_targeting_loop(ws, inp, F6_ROWS)
        ticks(gl, clock, 2)
        assert inp.sent_lines.count("K F6") == 1
        wolf2 = Monster("Wolf", 100.0, battle_list_row=1)
        with ws.lock():
            ws.monsters = [wolf2]  # the first wolf died
            ws.selected_target = None
        ticks(gl, clock, 1)
        with ws.lock():
            ws.selected_target = wolf2  # the select click shows
        ticks(gl, clock, 6)  # 0.35 s later, well inside the old 2 s cooldown
        assert inp.sent_lines.count("K F6") == 2

    def test_area_rule_before_the_row_hotkey(self) -> None:
        ws = self._wolf_selected()
        with ws.lock():
            ws.creature_slots = ((6, 5), (8, 6))
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp, F6_ROWS)
        ticks(gl, clock, 5)
        assert inp.sent_lines == ["K F5"]

    def test_no_key_without_a_selection(self) -> None:
        ws = self._wolf_selected()
        with ws.lock():
            ws.selected_target = None
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp, F6_ROWS)
        ticks(gl, clock, 1)  # the select click starts: no attack key
        assert all(line.startswith(("A ", "C ")) for line in inp.sent_lines)
        ticks(gl, clock, 30)  # the click went out, the outline never shows
        assert "K F6" not in inp.sent_lines

    def test_no_key_while_luring(self) -> None:
        cavebot = SimpleNamespace(
            is_active=lambda: False,
            luring=lambda: True,
            route=SimpleNamespace(current=0),
            release_walk_key=lambda: None,
            alarm=None,
        )
        inp = FakeInput()
        gl, clock = make_targeting_loop(self._wolf_selected(), inp, F6_ROWS, cavebot=cavebot)
        ticks(gl, clock, 20)
        assert inp.sent_lines == []


    def test_stuck_cavebot_alerts_once_and_attack_goes_on(self) -> None:
        cavebot = SimpleNamespace(
            is_active=lambda: False,
            luring=lambda: False,
            route=SimpleNamespace(current=0),
            release_walk_key=lambda: None,
            alarm="stuck",
        )
        alerts: list[str] = []
        inp = FakeInput()
        gl, clock = make_targeting_loop(self._wolf_selected(), inp, F6_ROWS, cavebot=cavebot)
        gl._alert_sink = alerts.append
        ticks(gl, clock, 40)
        assert gl.current_state is BotState.ATTACK
        assert "K F6" in inp.sent_lines
        assert alerts == ["cavebot alarm: stuck"]


class TestChaseMode:
    @staticmethod
    def _wolf_selected(chase_mode_on_screen: str | None) -> WorldState:
        ws = make_state()
        wolf = Monster("Wolf", 50.0)
        with ws.lock():
            ws.monsters = [wolf]
            ws.selected_target = wolf
            ws.chase_mode = chase_mode_on_screen
        return ws

    def test_wrong_mode_presses_the_toggle_and_holds_the_attack_until_it_shows(self) -> None:
        ws = self._wolf_selected("none")
        inp = FakeInput()
        gl, clock = make_targeting_loop(ws, inp, F6_ROWS)  # rows chase ("follow")
        ticks(gl, clock, 5)  # 0.25 s: inside the settle time, no second press
        assert inp.sent_lines == ["K F9"]
        with ws.lock():
            ws.chase_mode = "follow"
        ticks(gl, clock, 1)
        assert inp.sent_lines == ["K F9", "K F6"]

    def test_toggle_is_pressed_again_when_the_screen_still_shows_the_old_mode(self) -> None:
        inp = FakeInput()
        gl, clock = make_targeting_loop(self._wolf_selected("none"), inp, F6_ROWS)
        ticks(gl, clock, 12)  # 0.6 s > the 0.5 s settle time
        assert inp.sent_lines == ["K F9", "K F9"]

    def test_stand_still_row_toggles_away_from_chase(self) -> None:
        config = replace(F6_ROWS, default_monster=MonsterRow(hotkey="F6", movement="none"))
        inp = FakeInput()
        gl, clock = make_targeting_loop(self._wolf_selected("follow"), inp, config)
        ticks(gl, clock, 1)
        assert inp.sent_lines == ["K F9"]

    def test_right_mode_or_unread_mode_attacks_without_the_toggle(self) -> None:
        for chase_mode_on_screen in ("follow", None):
            inp = FakeInput()
            gl, clock = make_targeting_loop(self._wolf_selected(chase_mode_on_screen), inp, F6_ROWS)
            ticks(gl, clock, 1)
            assert inp.sent_lines == ["K F6"]

    def test_no_toggle_key_attacks_and_warns_once(self, caplog: pytest.LogCaptureFixture) -> None:
        inp = FakeInput()
        ws = self._wolf_selected("none")
        gl, clock = make_targeting_loop(ws, inp, F6_ROWS, toggle_chase_hotkey="")
        with caplog.at_level(logging.WARNING, logger="moonlight.core.loop"):
            ticks(gl, clock, 40)
        assert inp.sent_lines[0] == "K F6"
        assert "K F9" not in inp.sent_lines
        warnings = [r for r in caplog.records if "Toggle Chase Mode" in r.getMessage()]
        assert len(warnings) == 1

    def test_pause_sends_no_key(self) -> None:
        inp = FakeInput()
        gl, clock = make_targeting_loop(self._wolf_selected("none"), inp, F6_ROWS)
        gl.paused = True
        ticks(gl, clock, 60)
        assert inp.sent_lines == []


class TestGameLoopRun:
    def test_run_stays_paused_while_screen_not_ok(self) -> None:
        ws = make_state(screen_ok=False)
        inp = FakeInput()
        gl, clock = make_loop(ws, inp)
        ticks_wanted = int(LoopConfig().pause_timeout * 20) + 40

        def sleep(seconds: float) -> None:
            clock.sleep(seconds)
            if cast(int, gl.get_stats()["tick_count"]) >= ticks_wanted:
                gl.stop("test over")

        gl._sleep_fn = sleep
        gl.run()
        assert cast(int, gl.get_stats()["tick_count"]) == ticks_wanted  # never stopped itself
        assert gl.current_state is BotState.PAUSED
        assert set(inp.sent_lines) == {"X"}  # releases only

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
        for _ in range(3):
            gl.tick()  # no time passes: spell and potion once, then both on cooldown
        stats = gl.get_stats()
        assert stats["state"] == "attack"
        assert stats["tick_count"] == 3
        assert stats["actions_sent"] == 2
        assert stats["actions_dropped_rate_limit"] == 0  # attacking sends nothing
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


#: A cavebot that is on but walks nowhere: looting runs only with the cavebot on.
IDLE_CAVEBOT = SimpleNamespace(
    route=SimpleNamespace(current=0),
    release_walk_key=lambda: None,
    is_active=lambda: False,
    luring=lambda: False,
    stuck_on_creatures=False,
    on_target_killed=lambda: None,
    alarm=None,
)


class TestCurrentTask:
    def _task(self, ws: WorldState, modules: DecisionModules | None = None) -> object:
        if modules is None:
            gl, _ = make_loop(ws, FakeInput())
        else:
            gl, _ = make_loop(ws, FakeInput(), modules=modules)
        gl.tick()
        return gl.get_stats()["current_task"]

    def test_idle(self) -> None:
        assert self._task(make_state()) == "Idle"

    def test_healing(self) -> None:
        assert self._task(make_state(hp=50.0)) == "Healing"

    def test_attacking(self) -> None:
        assert self._task(make_state(target=True)) == "Attacking"

    def test_looting(self) -> None:
        looting = SimpleNamespace(
            should_loot=lambda s: True,
            loot_first=lambda s: True,
            ring_due=lambda reach, snap: False,
            step=lambda s: None,
        )
        cavebot = cast(Cavebot, IDLE_CAVEBOT)
        modules = DecisionModules(looting=cast(Looting, looting), cavebot=cavebot)
        assert self._task(make_state(), modules=modules) == "Looting"

    def test_walking_to_waypoint(self) -> None:
        cavebot = SimpleNamespace(
            route=SimpleNamespace(current=2),
            release_walk_key=lambda: None,
            is_active=lambda: True,
            luring=lambda: False,
            step=lambda: None,
            resume=lambda after_fight=False: None,
            alarm=None,
        )
        modules = DecisionModules(cavebot=cast(Cavebot, cavebot))
        assert self._task(make_state(), modules=modules) == "Walking to waypoint 3"



def test_loot_ring_reach_is_two_tiles() -> None:
    ws = make_state()
    rat = Monster("Rat")
    with ws.lock():
        ws.monsters = [rat, Monster("Cave Rat", battle_list_row=1)]
        ws.selected_target = rat
    checks: list[range] = []

    def ring_due(reach: range, snap: WorldState) -> bool:
        checks.append(reach)
        return False

    looting = SimpleNamespace(
        should_loot=lambda s: False,
        loot_first=lambda s: False,
        ring_due=ring_due,
    )
    modules = DecisionModules(looting=cast(Looting, looting), cavebot=cast(Cavebot, IDLE_CAVEBOT))
    gl, clock = make_loop(ws, FakeInput(), modules=modules)
    gl.tick()
    with ws.lock():
        ws.monsters, ws.selected_target = [], None  # the target died
    gl.tick()
    assert checks == [range(0, 2), range(0, 2)]  # 3x3, mid-fight or not

# doc: docs/prs/extras.md#extras
class TestExtras:
    @staticmethod
    def extras(clock: FakeClock, config: ExtrasConfig, *icons: str) -> Extras:
        names = ("hungry", "haste", "pz", "buff", "ring_empty", "amulet_empty", "pm")
        e = Extras(config, clock=clock, pictures={n: None for n in names})  # type: ignore[misc]
        e.icons = frozenset(icons)
        return e

    def test_food_idle_and_haste_only_while_walking(self) -> None:
        clock = FakeClock()
        config = ExtrasConfig(food=True, food_hotkey="F9", haste=True, haste_hotkey="F8")
        extras = self.extras(clock, config, "hungry")
        inp = FakeInput()
        walking = {"on": False}
        gl, _ = make_loop(
            make_state(),
            inp,
            clock=clock,
            modules=DecisionModules(extras=extras),
            move_provider=lambda s: walking["on"],
            move_action=lambda: None,
        )
        gl.tick()
        assert inp.sent_lines == ["K F9"]  # idle: no haste
        extras.icons = frozenset()
        walking["on"] = True
        clock.advance(1.0)
        gl.tick()
        assert gl.current_state is BotState.MOVE and inp.sent_lines == ["K F9", "K F8"]

    # doc: docs/prs/pause-stop.md#action-order
    def test_extras_wait_for_the_fight_and_for_a_script(self, tmp_path: Path) -> None:
        clock = FakeClock()
        extras = self.extras(clock, ExtrasConfig(food=True, food_hotkey="F9"), "hungry")
        (tmp_path / "s.py").write_text("def run(bot):\n    yield bot.press('f7')\n")
        scripts = ScriptRunner(tmp_path, timers=[("s", 60.0)], clock=clock)
        ws = make_state()
        wolf = Monster("Wolf", 50.0)
        with ws.lock():
            ws.monsters = [wolf]
            ws.selected_target = wolf
        inp = FakeInput()
        gl, _ = make_loop(
            ws, inp, clock=clock, modules=DecisionModules(extras=extras, scripts=scripts)
        )
        gl.tick()
        assert gl.current_state is BotState.ATTACK and inp.sent_lines == []  # no food mid-fight
        with ws.lock():
            ws.monsters = []
            ws.selected_target = None
        for _ in range(4):
            clock.advance(0.2)
            gl.tick()
        assert inp.sent_lines == ["K f7", "K F9"]  # the script first, then the food

    # doc: docs/prs/near-targeting.md#haste-in-fight
    def test_haste_but_no_food_in_a_fight_when_nothing_else_is_sent(self) -> None:
        clock = FakeClock()
        config = ExtrasConfig(food=True, food_hotkey="F9", haste=True, haste_hotkey="F8")
        extras = self.extras(clock, config, "hungry")
        ws = make_state()
        wolf = Monster("Wolf", 50.0)
        with ws.lock():
            ws.monsters = [wolf]
            ws.selected_target = wolf
        inp = FakeInput()
        gl, _ = make_loop(ws, inp, clock=clock, modules=DecisionModules(extras=extras))
        gl.tick()
        assert gl.current_state is BotState.ATTACK and inp.sent_lines == ["K F8"]

    # doc: docs/prs/near-targeting.md#same-tick
    def test_no_haste_in_a_fight_while_a_heal_is_due_even_on_cooldown(self) -> None:
        clock = FakeClock()
        extras = self.extras(clock, ExtrasConfig(haste=True, haste_hotkey="F8"))
        ws = make_state(hp=30.0)
        wolf = Monster("Wolf", 50.0)
        with ws.lock():
            ws.monsters = [wolf]
            ws.selected_target = wolf
        inp = FakeInput()
        modules = DecisionModules(extras=extras, healing=HealingDecision(clock=clock))
        gl, _ = make_loop(ws, inp, clock=clock, modules=modules)
        gl.tick()
        clock.advance(0.1)
        gl.tick()  # the heals are on cooldown, still due
        assert inp.sent_lines == ["K F2", "K F3"]
        ws.hp_percent = 100.0
        clock.advance(0.1)
        gl.tick()
        assert inp.sent_lines == ["K F2", "K F3", "K F8"]

    def test_a_player_alarms_only_after_staying_and_sends_to_refill(self) -> None:
        clock = FakeClock()
        config = ExtrasConfig(player_minutes=1.0, player_alarm=True, player_refill=True)
        cavebot = SimpleNamespace(
            is_active=lambda: False,
            luring=lambda: False,
            resume=lambda after_fight=False: None,
            release_walk_key=lambda: None,
            alarm=None,
            refill_now=False,
        )
        ws = make_state(players=1)
        ws.players = ("abc",)
        modules = DecisionModules(extras=self.extras(clock, config), cavebot=cast(Cavebot, cavebot))
        alerts: list[str] = []
        gl, _ = make_loop(ws, FakeInput(), clock=clock, modules=modules, alert=alerts.append)
        gl.tick()
        assert alerts == [] and not cavebot.refill_now
        clock.advance(60.0)
        gl.tick()
        gl.tick()
        assert cavebot.refill_now
        assert alerts == ["ALARM: player in sight over 1 min"]



# doc: docs/prs/rotation.md#rotation
class TestRotation:
    def test_the_switch_runs_on_the_login_screen_without_pausing(self) -> None:
        clock = FakeClock()
        accounts = (Account("a", "p", minutes=1.0), Account("b", "q"))
        scripts = ScriptRunner(clock=clock)
        scripts.rotation = Rotation(RotationConfig(accounts=accounts), clock=clock)
        ws = make_state()
        inp = FakeInput()
        gl, _ = make_loop(ws, inp, clock=clock, modules=DecisionModules(scripts=scripts))
        clock.advance(60.0)
        assert scripts.start("rotate")
        for _ in range(3):
            gl.tick()
            clock.advance(0.2)
        assert inp.sent_lines == ["K ctrl+l"]
        ws.screen_ok = False  # logged out
        for _ in range(30):
            gl.tick()
            clock.advance(0.2)
        assert gl.current_state is not BotState.PAUSED and "K tab" in inp.sent_lines
