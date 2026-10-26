"""T40: decision modules wired into GameLoop (each module is actually called).

Modules are ``unittest.mock`` doubles (the loop only calls their public
API) plus one real ``HealingDecision`` to prove the spell -> combo path.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from moonlight.core.loop import BotState, DecisionModules, GameLoop, LoopConfig
from moonlight.core.state import Monster, WorldState
from moonlight.hardware.input import FakeInput
from moonlight.healing.healing import HealingConfig, HealingDecision


def make_state(*, hp: float = 100.0, target: str | None = None, monsters: int = 0) -> WorldState:
    ws = WorldState()
    with ws.lock():
        ws.screen_ok = True
        ws.client_focused = True
        ws.hp_percent = hp
        ws.selected_target = Monster(name=target) if target else None
        ws.monsters = [Monster(name=f"m{i}") for i in range(monsters)]
    return ws


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, dt: float) -> None:
        self.now += max(0.0, dt)


def make_loop(ws: WorldState, inp: FakeInput, **modules: object) -> tuple[GameLoop, Clock]:
    clock = Clock()
    gl = GameLoop(
        ws,
        inp,
        LoopConfig(enable_f12_monitor=False),
        clock=clock,
        sleep_fn=clock.sleep,
        modules=DecisionModules(**modules),  # type: ignore[arg-type]
    )
    return gl, clock


def idle_module(**overrides: object) -> mock.Mock:
    """A module double that decides nothing unless told otherwise."""
    m = mock.Mock()
    m.decide.return_value = None
    m.track.return_value = []
    m.should_loot.return_value = False
    m.should_refill.return_value = False
    m.is_active.return_value = False
    m.send.return_value = True
    m.alarm = None
    m.route.current = 0
    m.config = HealingConfig()
    m.configure_mock(**overrides)
    return m


def test_healing_decide_and_send_every_tick_even_in_alarm() -> None:
    ws = make_state(hp=10.0)
    with ws.lock():
        ws.players_on_screen = 1  # ALARM
    inp = FakeInput()
    healing = HealingDecision(HealingConfig(), spell_combos={"exura gran": "F1", "exura": "F2"})
    gl, _ = make_loop(ws, inp, healing=healing)
    gl.tick()
    assert gl.current_state is BotState.ALARM
    assert "K F1" in inp.sent_lines


def test_healing_module_replaces_builtin_heal_combo() -> None:
    inp = FakeInput()
    healing = idle_module()
    gl, _ = make_loop(make_state(hp=50.0), inp, healing=healing)
    gl.tick()
    healing.decide.assert_called_once()
    assert gl.current_state is BotState.NORMAL_HEAL
    assert "K 2" not in inp.sent_lines  # built-in normal_heal_combo skipped


def test_targeting_decide_feeds_attack() -> None:
    inp = FakeInput()
    action = SimpleNamespace(action="attack", target_name="Wolf", reason="lowest hp")
    targeting = idle_module(**{"decide.return_value": action})
    gl, _ = make_loop(make_state(monsters=1), inp, targeting=targeting)
    gl.tick()
    targeting.decide.assert_called_once()
    assert gl.current_state is BotState.ATTACK
    assert "K r" in inp.sent_lines


def test_targeting_flee_enters_alarm() -> None:
    action = SimpleNamespace(action="flee", target_name=None, reason="player")
    targeting = idle_module(**{"decide.return_value": action})
    gl, _ = make_loop(make_state(), FakeInput(), targeting=targeting)
    gl.tick()
    assert gl.current_state is BotState.ALARM


def alarm(severity: str) -> SimpleNamespace:
    return SimpleNamespace(type=SimpleNamespace(value="pm_open"), severity=severity, message="m")


def test_critical_alarm_enters_alarm_warning_does_not() -> None:
    alarms = idle_module(**{"decide.return_value": alarm("critical")})
    gl, _ = make_loop(make_state(), FakeInput(), alarms=alarms)
    gl.tick()
    assert gl.current_state is BotState.ALARM

    alarms = idle_module(**{"decide.return_value": alarm("warning")})
    gl, _ = make_loop(make_state(), FakeInput(), alarms=alarms)
    gl.tick()
    alarms.decide.assert_called_once()
    assert gl.current_state is BotState.IDLE


def test_buffs_track_and_send_when_not_busy() -> None:
    buffs = idle_module(**{"track.return_value": ["haste"]})
    gl, _ = make_loop(make_state(), FakeInput(), buffs=buffs)
    gl.tick()
    buffs.track.assert_called_once()
    buffs.send.assert_called_once_with("haste", gl.input)


def test_buffs_skipped_while_healing_state() -> None:
    buffs = idle_module(**{"track.return_value": ["haste"]})
    gl, _ = make_loop(make_state(hp=50.0), FakeInput(), buffs=buffs)
    gl.tick()
    buffs.track.assert_not_called()


def test_kill_detection_and_loot_state() -> None:
    ws = make_state(target="Wolf", monsters=0)
    looting = idle_module()
    gl, clock = make_loop(ws, FakeInput(), looting=looting)
    gl.tick()
    with ws.lock():
        ws.selected_target = None  # Wolf vanished from the battle list
    looting.should_loot.return_value = True
    clock.now += 1.0
    gl.tick()
    looting.on_target_died.assert_called_once_with("Wolf")
    assert gl.current_state is BotState.LOOT
    looting.step.assert_called_once()


def test_refill_start_step_and_resume_cavebot() -> None:
    cavebot = idle_module()
    refill = idle_module(**{"should_refill.return_value": True, "step.return_value": False})
    refill.phase = "done"
    gl, _ = make_loop(make_state(), FakeInput(), refill=refill, cavebot=cavebot)
    gl.tick()
    assert gl.current_state is BotState.REFILL
    refill.start.assert_called_once()
    refill.step.assert_called_once()
    cavebot.start.assert_called_once()  # route resumed after the trip


def test_cavebot_is_default_move_provider() -> None:
    cavebot = idle_module(**{"is_active.return_value": True})
    gl, _ = make_loop(make_state(), FakeInput(), cavebot=cavebot)
    gl.tick()
    assert gl.current_state is BotState.MOVE
    cavebot.step.assert_called_once()


def test_swapped_cavebot_is_used_next_tick() -> None:
    old = idle_module(**{"is_active.return_value": True})
    new = idle_module(**{"is_active.return_value": True})
    gl, _ = make_loop(make_state(), FakeInput(), cavebot=old)
    gl.modules = DecisionModules(cavebot=new)
    gl.tick()
    new.step.assert_called_once()
    old.step.assert_not_called()


def test_perceive_runs_before_snapshot() -> None:
    ws = make_state()
    with ws.lock():
        ws.screen_ok = False

    def perceive() -> None:
        with ws.lock():
            ws.screen_ok = True
            ws.hp_percent = 50.0

    clock = Clock()
    gl = GameLoop(
        ws, FakeInput(), LoopConfig(enable_f12_monitor=False),
        clock=clock, sleep_fn=clock.sleep, perceive=perceive,
    )
    gl.tick()
    assert gl.current_state is BotState.NORMAL_HEAL
