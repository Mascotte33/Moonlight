"""T40: decision modules wired into GameLoop (each module is actually called).

Modules are ``unittest.mock`` doubles (the loop only calls their public
API) plus real ``HealingDecision`` instances to prove the heal hotkey path.
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
    m.ring_due.return_value = False
    m.loot_first.return_value = False
    m.is_active.return_value = False
    m.send.return_value = True
    m.alarm = None
    m.route.current = 0
    m.configure_mock(**overrides)
    return m


def test_healing_decide_and_send_every_tick_even_in_alarm() -> None:
    ws = make_state(hp=10.0)
    with ws.lock():
        ws.players_on_screen = 1  # ALARM
    inp = FakeInput()
    gl, _ = make_loop(ws, inp, healing=HealingDecision(HealingConfig()))
    gl.tick()
    assert gl.current_state is BotState.ALARM
    assert inp.sent_lines == ["K F2", "K F3"]


def test_targeting_decide_feeds_attack() -> None:
    action = SimpleNamespace(kind="select", battle_list_row=2, reason="lowest hp")
    targeting = idle_module(**{"decide.return_value": action, "row_center.return_value": (5, 6)})
    clicker = mock.Mock(busy=False, **{"step.return_value": None})
    gl, _ = make_loop(make_state(monsters=3), FakeInput(), targeting=targeting, clicker=clicker)
    gl.tick()
    targeting.decide.assert_called_once()
    assert gl.current_state is BotState.ATTACK
    targeting.row_center.assert_called_once_with(2)
    clicker.request.assert_called_once_with((5, 6))


def test_stuck_cavebot_hands_over_to_stuck_targeting_until_a_kill() -> None:
    action = SimpleNamespace(kind="select", battle_list_row=0, reason="all others")
    stuck_targeting = idle_module(
        **{"decide.return_value": action, "row_center.return_value": (5, 6)}
    )
    cavebot = idle_module(stuck_on_creatures=True, **{"luring.return_value": False})
    clicker = mock.Mock(busy=False, **{"step.return_value": None})
    ws = make_state(monsters=1)
    gl, clock = make_loop(
        ws, FakeInput(), stuck_targeting=stuck_targeting, cavebot=cavebot, clicker=clicker
    )
    gl.tick()  # targeting is off, the cavebot is stuck: the stuck rows decide
    assert gl.current_state is BotState.ATTACK
    with ws.lock():
        ws.selected_target = Monster(name="m0")
    clock.now += 1.0
    gl.tick()
    assert cavebot.stuck_on_creatures
    with ws.lock():
        ws.selected_target = None  # killed
    clock.now += 1.0
    gl.tick()
    assert not cavebot.stuck_on_creatures  # the cavebot looks for a way before the next fight
    assert stuck_targeting.decide.call_count == 2


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


def test_kill_detected_while_a_same_named_monster_remains() -> None:
    ws = make_state(target="Wolf")
    with ws.lock():
        ws.monsters = [Monster(name="Wolf"), Monster(name="Wolf")]
    looting = idle_module()
    gl, clock = make_loop(ws, FakeInput(), looting=looting)
    gl.tick()
    with ws.lock():
        ws.monsters = [Monster(name="Wolf")]  # one died, the client selects the other
    clock.now += 1.0
    gl.tick()
    looting.on_target_died.assert_called_once_with("Wolf")


def test_kill_mid_fight_loots_before_next_target() -> None:
    ws = make_state(target="Wolf", monsters=2)
    looting = idle_module(**{"loot_first.return_value": True, "should_loot.return_value": True})
    gl, _ = make_loop(ws, FakeInput(), looting=looting)
    gl.tick()
    assert gl.current_state is BotState.LOOT  # not ATTACK on the next wolf
    looting.step.assert_called_once()


def test_ring_press_does_not_stop_the_attack() -> None:
    action = SimpleNamespace(kind="select", battle_list_row=0, reason="r")
    targeting = idle_module(**{"decide.return_value": action, "row_center.return_value": (5, 6)})
    looting = idle_module(**{"ring_due.return_value": True})
    clicker = mock.Mock(busy=False, **{"step.return_value": None})
    gl, _ = make_loop(
        make_state(monsters=1), FakeInput(), targeting=targeting, looting=looting, clicker=clicker
    )
    gl.tick()
    looting.press_ring.assert_called_once()
    assert gl.current_state is BotState.ATTACK
    clicker.request.assert_called_once_with((5, 6))


def test_alarm_still_loots() -> None:
    ws = make_state()
    with ws.lock():
        ws.players_on_screen = 1  # ALARM
    looting = idle_module(**{"should_loot.return_value": True})
    gl, _ = make_loop(ws, FakeInput(), looting=looting)
    gl.tick()
    assert gl.current_state is BotState.ALARM
    looting.step.assert_called_once()


def test_emptied_battle_list_queues_a_loot() -> None:
    ws = make_state(monsters=2)
    looting = idle_module()
    gl, clock = make_loop(ws, FakeInput(), looting=looting)
    gl.tick()
    with ws.lock():
        ws.monsters = []
    clock.now += 1.0
    gl.tick()
    looting.on_target_died.assert_called_once_with("")


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
    assert gl.current_state is BotState.IDLE  # perceive set screen_ok, so not PAUSED
