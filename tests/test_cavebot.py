"""Unit tests for moonlight.cavebot.cavebot (T22).

Offline: a tiny simulated world (``Sim``) renders synthetic frames -- a
radar with the player marker at its center (x/y come from an injected
MapMatcher) and a floor indicator drawn from an injected TemplateSet.
Clicks go to a recording ClickController; hotkeys go to FakeInput.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from moonlight.cavebot.cavebot import Cavebot, Route, RouteError, RouteFileLoader, Waypoint
from moonlight.cavebot.pathing import CostMap, next_step
from moonlight.core.loop import BotState, DecisionModules, GameLoop, LoopConfig
from moonlight.core.safety import RateLimitedInput, RateLimiter
from moonlight.core.state import Monster, Position, WorldState
from moonlight.hardware.clicker import ClickController
from moonlight.hardware.input import ClickSide, FakeInput
from moonlight.scripts.scripts import ScriptRunner

MINIMAP = (10, 10, 120, 120)
GAME = (200, 0, 480, 352)  # 32 px tiles; player tile center = (440, 176)


class RecordingClicker(ClickController):
    """Records requested clicks; every click completes at once."""

    def __init__(self) -> None:
        self.clicks: list[tuple[tuple[int, int], ClickSide]] = []
        self.cancels = 0
        self._screen: tuple[int, int, int, int] | None = (0, 0, 1000, 1000)
        self._window_origin = None

    @property
    def busy(self) -> bool:
        return False

    def request(
        self,
        target: tuple[int, int],
        *,
        tolerance: int = 2,
        max_iters: int = 5,
        side: ClickSide = ClickSide.LEFT,
    ) -> None:
        self.clicks.append((target, side))

    def step(self) -> bool | None:
        return None

    def cancel(self) -> None:
        self.cancels += 1


class Sim:
    """Player position + clock + the Cavebot wired to synthetic readers."""

    def __init__(self, waypoints: list[Waypoint], start: Position, **kw: Any) -> None:
        self.now = 0.0
        self.fake = FakeInput()
        self.clicker = kw.pop("clicker", None) or RecordingClicker()
        self.state = WorldState(client_focused=True, screen_ok=True)
        self.reached: list[int] = []
        self._pos: Position | None = start
        self.state.position = start

        self.route = Route("test", "", waypoints, loop=False)
        self.bot = Cavebot(
            self.route,
            MINIMAP,
            self.clicker,
            self.state,
            game_region=GAME,
            input_backend=self.fake,
            clock=lambda: self.now,
            on_waypoint_reached=self.reached.append,
            **kw,
        )

    @property
    def pos(self) -> Position | None:
        return self._pos

    @pos.setter
    def pos(self, value: Position | None) -> None:
        self._pos = value
        with self.state.lock():
            self.state.position = (
                value if value is not None else Position(0, 0, 0)
            )

    def step(self, dt: float = 0.05) -> None:
        self.now += dt
        self.bot.step()


def walk(x: int, y: int, z: int = 7) -> Waypoint:
    return Waypoint(x, y, z, "walk")


# -- route loading -----------------------------------------------------------


VALID = {
    "name": "r",
    "description": "d",
    "waypoints": [
        {"action": "label", "label": "start"},
        {"x": 100, "y": 200, "z": 7, "action": "walk"},
        {"x": 101, "y": 200, "z": 7, "action": "rope"},
        {"action": "goto", "label": "start"},
    ],
}


def test_load_valid_route(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text(json.dumps(VALID), encoding="utf-8")
    route = RouteFileLoader.load(path)
    assert route.name == "r" and route.description == "d" and route.current == 0
    assert route.waypoints[1] == Waypoint(100, 200, 7, "walk")
    assert route.waypoints[2].action == "rope"
    assert route.waypoints[3] == Waypoint(0, 0, 0, "goto", "start")


README = Path(__file__).parents[1] / "data" / "waypoints" / "README.md"


@pytest.mark.skipif(not README.exists(), reason="READMEs are owner-local")
def test_readme_example_is_valid() -> None:
    readme = README
    example = readme.read_text(encoding="utf-8").split("```json")[1].split("```")[0]
    assert RouteFileLoader.parse(json.loads(example)).name == "rotworm_loop"


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {"waypoints": [{"x": 1, "y": 1, "z": 7}]},
        {"name": "r", "waypoints": []},
        {"name": "r", "waypoints": [{"x": 1, "y": 1, "z": 7, "action": "fly"}]},
        {"name": "r", "waypoints": [{"x": 1, "y": 1}]},
        {"name": "r", "waypoints": [{"x": 1.5, "y": 1, "z": 7}]},
        {"name": "r", "waypoints": [{"x": True, "y": 1, "z": 7}]},
        {"name": "r", "waypoints": [{"x": 1, "y": 1, "z": 16}]},
        {"name": "r", "waypoints": [{"action": "label", "label": "a"}]},
        {"name": "r", "waypoints": [{"x": 1, "y": 1, "z": 7}, {"action": "goto", "label": "x"}]},
        {"name": "r", "waypoints": [{"x": 1, "y": 1, "z": 7}, {"action": "label"}]},
        {
            "name": "r",
            "waypoints": [
                {"x": 1, "y": 1, "z": 7},
                {"action": "label", "label": "a"},
                {"action": "label", "label": "a"},
            ],
        },
    ],
)
def test_parse_invalid_route(raw: object) -> None:
    with pytest.raises(RouteError):
        RouteFileLoader.parse(raw)


def test_load_missing_and_bad_json(tmp_path: Path) -> None:
    with pytest.raises(RouteError):
        RouteFileLoader.load(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(RouteError):
        RouteFileLoader.load(bad)


def test_rope_route_without_input_backend_rejected() -> None:
    with pytest.raises(ValueError):
        Cavebot(
            Route("r", "", [Waypoint(1, 1, 7, "rope")]),
            MINIMAP,
            RecordingClicker(),
            WorldState(),
            game_region=GAME,
        )


# -- navigation ----------------------------------------------------------------


def test_walk_clicks_minimap_toward_waypoint_then_completes() -> None:
    sim = Sim([walk(105, 98)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    center = (MINIMAP[0] + MINIMAP[2] // 2, MINIMAP[1] + MINIMAP[3] // 2)
    assert sim.clicker.clicks == [((center[0] + 5, center[1] - 2), ClickSide.LEFT)]

    sim.pos = Position(104, 99, 7)  # within 1 tile counts as reached
    sim.step()
    assert sim.reached == [0]
    assert not sim.bot.is_active() and sim.bot.alarm is None


def test_looping_route_starts_over_after_the_last_waypoint() -> None:
    sim = Sim([walk(110, 100), walk(110, 110)], Position(110, 100, 7))
    sim.route.loop = True
    sim.bot.start()
    sim.step()
    sim.pos = Position(110, 110, 7)
    sim.step()
    assert sim.reached == [0, 1] and sim.route.current == 0 and sim.bot.is_active()


def test_far_waypoint_click_is_clamped_to_radar() -> None:
    sim = Sim([walk(1000, 100)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    (x, _y), _side = sim.clicker.clicks[0]
    assert MINIMAP[0] < x < MINIMAP[0] + MINIMAP[2]


def test_no_reclick_while_moving_reclick_when_idle() -> None:
    sim = Sim([walk(120, 100)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    for i in range(1, 30):  # walking: position changes every tick
        sim.pos = Position(100 + i // 3, 100, 7)
        sim.step()
    assert len(sim.clicker.clicks) == 1
    for _ in range(25):  # stand still 1.25 s > reclick_interval
        sim.step()
    assert len(sim.clicker.clicks) == 2


def test_multi_waypoint_route_runs_in_order() -> None:
    sim = Sim([walk(110, 100), walk(110, 110)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    sim.pos = Position(110, 100, 7)
    sim.step()
    assert sim.route.current == 1 and sim.bot.is_active()
    sim.step()
    sim.pos = Position(110, 110, 7)
    sim.step()
    assert sim.reached == [0, 1] and not sim.bot.is_active()


def test_label_goto_loops_route() -> None:
    wps = [
        Waypoint(0, 0, 0, "label", "start"),
        walk(110, 100),
        Waypoint(0, 0, 0, "goto", "start"),
    ]
    sim = Sim(wps, Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    sim.pos = Position(110, 100, 7)
    sim.step()
    sim.pos = Position(100, 100, 7)  # moved off: route must still be on wp 1
    sim.step()
    assert sim.bot.is_active() and sim.route.current == 1 and sim.reached == [1]


# doc: docs/prs/refill.md#check-waypoint
def test_check_jumps_to_town_once_on_refill_now() -> None:
    wps = [
        Waypoint(0, 0, 0, "label", "hunt"),
        walk(100, 100),
        Waypoint(0, 0, 0, "check", "town"),
        walk(110, 100),
        Waypoint(0, 0, 0, "goto", "hunt"),
        Waypoint(0, 0, 0, "label", "town"),
        walk(120, 100),
        Waypoint(0, 0, 0, "goto", "hunt"),
    ]
    sim = Sim(wps, Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    assert sim.route.current == 3  # not due: past the check
    sim.route.current = 1
    sim.bot.refill_now = True
    sim.step()
    assert sim.route.current == 6 and not sim.bot.refill_now
    sim.route.current = 1
    sim.step()
    assert sim.route.current == 3  # the jump used it up


def test_check_needs_an_existing_label() -> None:
    raw = {"name": "r", "waypoints": [{"x": 1, "y": 1, "z": 7}, {"action": "check", "label": "x"}]}
    with pytest.raises(RouteError, match="check target"):
        RouteFileLoader.parse(raw)


# doc: docs/prs/refill.md#say-key-waypoints
def test_say_types_one_key_per_tick_then_holds_before_moving_on() -> None:
    wps = [walk(100, 100), Waypoint(0, 0, 0, "say", "Hi Bob"), walk(110, 100)]
    sim = Sim(wps, Position(100, 100, 7))
    sim.bot.start()
    for _ in range(9):
        sim.step()
    assert sim.fake.sent_lines == ["K h", "K i", "K space", "K b", "K o", "K b", "K enter"]
    assert sim.route.current == 1
    sim.step(1.0)
    assert sim.route.current == 2


def test_key_resends_a_press_the_wire_limiter_dropped() -> None:
    sim = Sim([Waypoint(0, 0, 0, "key", "F5"), walk(110, 100)], Position(100, 100, 7))
    limited = RateLimitedInput(sim.fake, RateLimiter(rate=1.0, capacity=1.0, clock=lambda: sim.now))
    limited.reserve = 0.0
    limited.press_combo("F1")  # empties the bucket
    sim.bot._input = limited
    sim.bot.start()
    sim.step()
    assert sim.fake.sent_lines == ["K F1"]  # dropped
    sim.step(1.0)
    assert sim.fake.sent_lines == ["K F1", "K F5"]


def test_drag_moves_holds_moves_and_lets_go_one_command_per_tick() -> None:
    sim = Sim([Waypoint(0, 0, 0, "drag", "100 200 300 400"), walk(110, 100)], Position(100, 100, 7))
    sim.bot.start()
    for _ in range(5):
        sim.step()
    a, b = sim.clicker.to_wire((100, 200)), sim.clicker.to_wire((300, 400))
    assert sim.fake.sent_lines == [f"A {a[0]} {a[1]}", "H L", f"A {b[0]} {b[1]}", "R L"]
    sim.step(1.0)
    assert sim.route.current == 1


def test_drag_needs_the_absolute_clicker() -> None:
    clicker = RecordingClicker()
    clicker._screen = None
    with pytest.raises(ValueError, match="absolute"):
        Sim([Waypoint(0, 0, 0, "drag", "1 2 3 4")], Position(100, 100, 7), clicker=clicker)


# doc: docs/prs/scripts.md#script-priority
def test_wait_holds_the_route_for_its_seconds() -> None:
    sim = Sim([Waypoint(0, 0, 0, "wait", "2"), walk(110, 100)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    sim.step(1.5)
    assert sim.route.current == 0
    sim.step(1.0)
    assert sim.route.current == 1 and sim.fake.sent_lines == []


def test_script_waypoint_starts_the_script_then_moves_on(tmp_path: Path) -> None:
    (tmp_path / "s.py").write_text("def run(bot):\n    yield bot.goto('back')\n")
    runner = ScriptRunner(tmp_path)
    wps = [Waypoint(0, 0, 0, "script", "s"), walk(110, 100), Waypoint(0, 0, 0, "label", "back")]
    sim = Sim(wps, Position(100, 100, 7), scripts=runner)
    sim.bot.start()
    sim.step()
    assert runner.running == "s"
    runner.cavebot = sim.bot
    while runner.running:
        runner.step(sim.state, sim.fake)
    assert sim.route.current == 2  # bot.goto jumped the route


@pytest.mark.parametrize(
    ("action", "label"),
    [("say", "hi!"), ("key", "ctrl 1"), ("drag", "1 2 3"), ("wait", "soon"), ("script", "a b")],
)
def test_bad_say_or_key_label_refused(action: str, label: str) -> None:
    raw = {"name": "r", "waypoints": [{"x": 1, "y": 1, "z": 7}, {"action": action, "label": label}]}
    with pytest.raises(RouteError, match=action):
        RouteFileLoader.parse(raw)


# -- tile actions ------------------------------------------------------------


def test_rope_presses_hotkey_clicks_tile_and_waits_for_floor_up() -> None:
    sim = Sim([Waypoint(100, 100, 7, "rope"), walk(100, 100, 6)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    assert sim.fake.sent_lines == ["K F9"]
    assert sim.clicker.clicks == [((440, 176), ClickSide.LEFT)]
    sim.step()  # still on z 7: no spam before reclick_interval
    assert len(sim.clicker.clicks) == 1
    sim.pos = Position(100, 100, 6)
    sim.step()
    assert sim.reached[0] == 0


def test_ladder_right_clicks_adjacent_tile() -> None:
    sim = Sim([Waypoint(101, 99, 7, "ladder")], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    assert sim.clicker.clicks == [((440 + 32, 176 - 32), ClickSide.RIGHT)]
    assert sim.fake.sent_lines == []
    sim.pos = Position(101, 99, 6)
    sim.step()
    assert sim.reached == [0] and not sim.bot.is_active()


def test_shovel_digs_then_steps_in_and_expects_floor_down() -> None:
    sim = Sim([Waypoint(100, 101, 7, "shovel")], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    assert sim.fake.sent_lines == ["K F10"]
    assert sim.clicker.clicks == [((440, 208), ClickSide.LEFT)] * 2
    sim.pos = Position(100, 101, 8)
    sim.step()
    assert sim.reached == [0]


def test_ladder_down_a_sewer_grate_is_done() -> None:
    sim = Sim([Waypoint(100, 100, 7, "ladder"), walk(105, 100, 8)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    assert sim.clicker.clicks[0][1] is ClickSide.RIGHT
    sim.pos = Position(100, 100, 8)
    sim.step()
    assert sim.reached == [0] and sim.bot.route.current == 1


def test_step_presses_its_key_once_on_its_tile_then_moves_on() -> None:
    # doc: docs/prs/steps.md#step-waypoint
    wps = [Waypoint(100, 101, 7, "step", "down"), walk(110, 100, 8)]
    sim = Sim(wps, Position(100, 100, 7), walk_keys=True)
    sim.bot.start()
    sim.step()
    assert sim.fake.sent_lines == ["D down"]  # walks onto the step tile
    sim.pos = Position(100, 101, 7)
    for _ in range(10):
        sim.step()  # the step did not move the player: still one press
    assert sim.fake.sent_lines == ["D down", "U down", "K down"]
    sim.pos = Position(100, 102, 8)  # fell through the hole
    sim.step(1.0)
    assert sim.reached == [0] and sim.route.current == 1
    assert sim.fake.sent_lines.count("K down") == 1


def test_step_label_is_a_walk_key() -> None:
    raw = {"name": "r", "waypoints": [{"x": 1, "y": 1, "z": 7, "action": "step", "label": "up"}]}
    assert RouteFileLoader.parse(raw).waypoints[0].label == "up"
    raw["waypoints"][0]["label"] = "F5"  # type: ignore[index]
    with pytest.raises(RouteError, match="step label"):
        RouteFileLoader.parse(raw)


def test_action_retried_when_floor_does_not_change() -> None:
    sim = Sim([Waypoint(100, 100, 7, "ladder")], Position(100, 100, 7))
    sim.bot.start()
    for _ in range(25):
        sim.step()
    assert len(sim.clicker.clicks) == 2


# -- stuck / floor change / stop -----------------------------------------------


def test_stuck_same_position_raises_alarm_and_stops() -> None:
    sim = Sim([walk(120, 100)], Position(100, 100, 7))
    sim.bot.start()
    for _ in range(1190):  # 59.5 s standing still
        sim.step()
    assert sim.bot.is_active()
    for _ in range(20):
        sim.step()
    assert not sim.bot.is_active() and sim.bot.alarm == "stuck"
    clicks = len(sim.clicker.clicks)
    sim.step()
    assert len(sim.clicker.clicks) == clicks


def run_until(sim: Sim, seconds: float) -> None:
    while sim.now < seconds - 1e-9:
        sim.step()


def test_blocked_sidesteps_left_then_skips_walk_waypoint() -> None:
    sim = Sim([walk(120, 100), walk(140, 100)], Position(100, 100, 7))
    sim.bot.start()
    run_until(sim, 3.2)
    assert sim.clicker.clicks[-1] == ((70, 69), ClickSide.LEFT)  # north of an east walk
    sim.pos = Position(100, 99, 7)
    sim.step()
    assert sim.route.current == 1


def test_blocked_tries_right_side_second_then_still_goes_stuck() -> None:
    sim = Sim([walk(120, 100)], Position(100, 100, 7))
    sim.bot.start()
    run_until(sim, 6.2)
    targets = [t for t, _ in sim.clicker.clicks]
    assert (70, 69) in targets and targets[-1] == (70, 71)
    run_until(sim, 61.0)
    assert sim.bot.alarm == "stuck"
    targets = [t for t, _ in sim.clicker.clicks]
    assert targets.count((70, 69)) == 1 and targets.count((70, 71)) == 1


def test_blocked_non_walk_waypoint_is_retried_not_skipped() -> None:
    sim = Sim([Waypoint(120, 100, 7, "node"), walk(140, 100)], Position(100, 100, 7))
    sim.bot.start()
    run_until(sim, 3.2)
    sim.pos = Position(100, 99, 7)
    sim.step()
    assert sim.route.current == 0


def test_unreadable_position_sends_nothing_and_still_times_out() -> None:
    sim = Sim([walk(120, 100)], Position(100, 100, 7))
    sim.pos = None
    sim.bot.start()
    for _ in range(1220):
        sim.step()
    assert sim.clicker.clicks == [] and sim.bot.alarm == "stuck"


def test_unexpected_floor_change_resyncs_to_waypoint_on_new_floor() -> None:
    sim = Sim([walk(110, 100), walk(120, 100), walk(130, 100, 8)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    sim.pos = Position(100, 100, 8)  # fell through a hole
    sim.step()
    assert sim.route.current == 2 and sim.bot.is_active()
    sim.step()
    assert sim.clicker.clicks[-1][0][0] > MINIMAP[0] + MINIMAP[2] // 2  # walking east


def test_floor_change_with_no_matching_waypoint_alarms_lost() -> None:
    sim = Sim([walk(110, 100), walk(120, 100)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    sim.pos = Position(100, 100, 9)
    sim.step()
    assert not sim.bot.is_active() and sim.bot.alarm == "lost"


def test_go_to_walks_to_the_picked_waypoint_next() -> None:
    sim = Sim([walk(110, 100), walk(120, 100), walk(130, 100)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    sim.bot.go_to(2)
    sim.pos = Position(130, 100, 7)
    sim.step()
    assert sim.reached == [2] and not sim.bot.is_active()


def test_stop_mid_route_and_restart() -> None:
    sim = Sim([walk(110, 100), walk(120, 100)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    sim.pos = Position(110, 100, 7)
    sim.step()
    sim.bot.stop()
    clicks = len(sim.clicker.clicks)
    sim.step()
    assert not sim.bot.is_active() and len(sim.clicker.clicks) == clicks
    sim.bot.start()
    assert sim.route.current == 0 and sim.bot.is_active()


def test_screen_not_ok_sends_nothing() -> None:
    sim = Sim([walk(110, 100)], Position(100, 100, 7))
    sim.state.screen_ok = False
    sim.bot.start()
    sim.step()
    assert sim.clicker.clicks == []


def test_runs_from_game_loop_move_step() -> None:
    sim = Sim([walk(110, 100)], Position(100, 100, 7))
    loop = GameLoop(
        sim.state,
        sim.fake,
        LoopConfig(enable_f12_monitor=False),
        move_provider=lambda _s: sim.bot.is_active(),
        move_action=sim.bot.step,
    )
    sim.bot.start()
    loop.tick()
    assert len(sim.clicker.clicks) == 1
    sim.pos = Position(110, 100, 7)
    loop.tick()
    assert not sim.bot.is_active()


# -- F-06: stuck-timer reset on MOVE re-entry ---------------------------------


def test_resume_resets_stuck_timer_on_move_reentry() -> None:
    """F-06: re-entering MOVE calls resume(), which resets the stuck timer.

    Time spent in a fight/IDLE (the loop is NOT in MOVE, so the cavebot's
    step() is not called and only the clock advances) must not count toward
    "stuck". When MOVE is (re)entered the loop calls Cavebot.resume()
    (loop.py _enter_state), restarting _last_move so the first MOVE step
    after re-entry does not immediately halt as stuck.
    """
    sim = Sim([walk(120, 100)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()  # clock=0.05; _last_move set while in MOVE

    # Simulate a >stuck_timeout fight/IDLE: the loop leaves MOVE, so the
    # cavebot's step() is not called and only the clock advances.
    sim.now += 61.0  # 61 s > stuck_timeout (60 s)

    # The loop calls resume() when the state transitions back to MOVE.
    sim.bot.resume()

    # The first MOVE step after re-entry must NOT immediately halt as stuck.
    sim.step()
    assert sim.bot.alarm is None
    assert sim.bot.is_active()


def test_stuck_still_halts_without_resume_after_long_idle() -> None:
    """Contrast for F-06: without resume() a long idle WOULD halt as stuck.

    Proves the resume() call on MOVE re-entry (not the passage of time alone)
    is what prevents the false stuck halt -- i.e. the fix is load-bearing.
    """
    sim = Sim([walk(120, 100)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()  # clock=0.05; _last_move set

    # Long fight/IDLE with NO resume() (the bug: timer not reset).
    sim.now += 61.0
    sim.step()
    assert sim.bot.alarm == "stuck"
    assert not sim.bot.is_active()


def test_loop_calls_cavebot_resume_on_move_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-06 wiring: entering MOVE calls cavebot.resume() (loop._enter_state).

    Closes the "verify it is actually called" requirement with a test: a MOVE
    transition dispatches through _enter_state, which calls cavebot.resume()
    so a long fight/loot never trips the stuck halt on the first MOVE.
    """
    sim = Sim([walk(110, 100)], Position(100, 100, 7))
    loop = GameLoop(
        sim.state,
        sim.fake,
        LoopConfig(enable_f12_monitor=False),
        modules=DecisionModules(cavebot=sim.bot),
    )
    resumed: list[float] = []
    original_resume = sim.bot.resume

    def _spy_resume() -> None:
        original_resume()
        resumed.append(sim.now)

    monkeypatch.setattr(sim.bot, "resume", _spy_resume)
    sim.bot.start()
    loop.tick()
    assert loop.current_state is BotState.MOVE
    assert resumed, "cavebot.resume() not called on MOVE entry"


def test_busy_clicker_is_stepped_not_rerequested_and_cancelled_on_advance() -> None:
    """P5: a pending click is driven one step per tick, never re-queued."""

    class SlowClicker(RecordingClicker):
        def __init__(self) -> None:
            super().__init__()
            self.pending = 0
            self.steps = 0

        @property
        def busy(self) -> bool:
            return self.pending > 0

        def request(self, target: tuple[int, int], **kw: object) -> None:
            super().request(target)
            self.pending = 5

        def step(self) -> bool | None:
            self.steps += 1
            self.pending -= 1
            return None

        def cancel(self) -> None:
            super().cancel()
            self.pending = 0

    sim = Sim([walk(105, 100)], Position(100, 100, 7))
    slow = SlowClicker()
    sim.clicker = slow
    sim.bot._clicker = slow
    sim.bot.start()
    for _ in range(30):  # 1.5 s idle: past reclick_interval while busy
        sim.step()
        if len(slow.clicks) > 1:
            break
    assert slow.steps >= 5  # first step on request, then one per tick
    sim.pos = Position(105, 100, 7)
    cancels = slow.cancels
    sim.step()
    assert sim.reached == [0] and slow.cancels > cancels


# -- T3.F10.K1: node, stand and lure ------------------------------------------


@pytest.mark.parametrize(
    ("action", "near", "reached"),
    [("node", 3, True), ("node", 4, False), ("stand", 1, False), ("stand", 0, True)],
)
def test_arrival_distance_per_action(action: str, near: int, reached: bool) -> None:
    sim = Sim([Waypoint(110, 100, 7, action)], Position(100, 100, 7))
    sim.bot.start()
    sim.pos = Position(110 - near, 100, 7)
    sim.step()
    assert (sim.reached == [0]) is reached


def test_lure_waypoint_holds_attacks_until_reached() -> None:
    sim = Sim([Waypoint(110, 100, 7, "lure"), walk(120, 100)], Position(100, 100, 7))
    sim.state.selected_target = Monster(name="Wolf")
    loop = GameLoop(
        sim.state,
        sim.fake,
        LoopConfig(enable_f12_monitor=False),
        modules=DecisionModules(cavebot=sim.bot),
    )
    sim.bot.start()
    assert sim.bot.luring()
    loop.tick()
    assert loop.current_state is BotState.MOVE
    sim.pos = Position(110, 100, 7)
    loop.tick()  # lure reached; the next waypoint is a plain walk
    assert not sim.bot.luring()
    loop.tick()
    assert loop.get_stats()["state"] == BotState.ATTACK.value


# -- T8.F5.K1: stairs ---------------------------------------------------------


def test_stairs_walks_onto_the_tile_and_is_done_on_a_floor_change() -> None:
    sim = Sim([Waypoint(105, 100, 7, "stairs"), walk(110, 100, 6)], Position(100, 100, 7))
    sim.bot.start()
    sim.pos = Position(104, 100, 7)
    sim.step()
    assert sim.reached == []  # next to the stairs is not on them
    sim.pos = Position(105, 100, 6)  # stepped on, went up
    sim.step()
    assert sim.reached == [0] and sim.route.current == 1 and sim.bot.is_active()


# -- arrow-key walking ------------------------------------------------------

def pressed(sim: Sim) -> list[str]:
    """The walk keys pressed down, in order (a held key's release is left out)."""
    return [line[2:] for line in sim.fake.sent_lines if line.startswith("D ")]


#: Steps of 0.05 s for two presses of 0.7 s that did not move: blocked.
_BLOCK_STEPS = 30


def test_walk_keys_needs_an_input_backend() -> None:
    route = Route("test", "", [walk(100, 103)], loop=False)
    state = WorldState(client_focused=True, screen_ok=True)
    with pytest.raises(ValueError, match="walk_keys"):
        Cavebot(route, MINIMAP, RecordingClicker(), state, game_region=GAME, walk_keys=True)


def test_walk_keys_presses_one_arrow_per_step() -> None:
    sim = Sim([walk(100, 104)], Position(100, 100, 7), walk_keys=True)
    sim.bot.start()
    sim.step()
    assert pressed(sim) == ["down"] and sim.clicker.clicks == []
    sim.step()  # still on the old tile: no second press yet
    assert pressed(sim) == ["down"]
    sim.pos = Position(100, 101, 7)
    sim.step()  # moved: next step at once
    assert pressed(sim) == ["down"] * 2
    for _ in range(15):  # 0.7 s without moving: maybe a lag, press once more
        sim.step()
    assert pressed(sim)[2] == "down"
    for _ in range(15):  # still not moved: blocked, press around it
        sim.step()
    assert pressed(sim)[3] in ("left", "right")


def test_walk_keys_hold_the_key_until_the_way_turns_or_stops() -> None:
    sim = Sim([walk(100, 103), walk(96, 102)], Position(100, 100, 7), walk_keys=True)
    sim.bot.start()
    sim.step()
    sim.pos = Position(100, 101, 7)
    sim.step()  # same way on: the key stays down
    assert sim.fake.sent_lines == ["D down", "D down"]
    sim.pos = Position(100, 102, 7)
    sim.step()  # waypoint 0 reached
    sim.step()  # waypoint 1 is left: let go of down, hold left
    assert sim.fake.sent_lines[2:] == ["U down", "D left"]
    sim.state.selected_target = Monster("Rat", 100.0)
    sim.step()  # a fight: let go
    assert sim.fake.sent_lines[-1] == "U left"
    sim.bot.stop()
    assert sim.fake.sent_lines[-1] == "U left"  # nothing held twice


def test_walk_keys_follow_the_cost_map_around_a_wall(tmp_path: Path) -> None:
    tile = np.full((256, 256, 3), 100, np.uint8)
    tile[0:10, 4] = (0, 255, 255)  # wall at x = 4, y 0..9; walk around below it
    cv2.imwrite(str(tmp_path / "0_0_7.png"), tile)
    costs = CostMap(tmp_path)
    assert next_step((3, 5, 7), (6, 5, 7), costs.walkable) == ((0, 1), 13)
    assert next_step((3, 5, 7), (3, 2, 7), costs.walkable) == ((0, -1), 3)
    sim = Sim([walk(6, 5)], Position(3, 5, 7), walk_keys=True, cost_map=costs)
    sim.bot.start()
    sim.step()
    assert pressed(sim) == ["down"]


def test_cost_map_no_path_when_walled_in(tmp_path: Path) -> None:
    tile = np.full((256, 256, 3), (0, 255, 255), np.uint8)
    tile[50, 50] = 100
    cv2.imwrite(str(tmp_path / "0_0_7.png"), tile)
    assert next_step((50, 50, 7), (60, 50, 7), CostMap(tmp_path).walkable, margin=5) is None
    assert CostMap(tmp_path / "missing").empty


def test_walk_keys_remember_every_blocked_tile() -> None:
    sim = Sim([walk(100, 104)], Position(100, 100, 7), walk_keys=True)
    sim.bot.start()
    sim.step()
    assert pressed(sim) == ["down"]
    for _ in range(_BLOCK_STEPS):  # an NPC stands below: try around it
        sim.step()
    assert pressed(sim)[-1] == "left"
    sim.pos = Position(99, 100, 7)
    sim.step()
    assert pressed(sim)[-1] == "down"
    for _ in range(_BLOCK_STEPS):  # that way is blocked too: not down again, not back right
        sim.step()
    assert pressed(sim)[-1] == "left"
    sim.pos = Position(98, 100, 7)
    sim.step()
    assert pressed(sim)[-1] == "down"


def test_walk_keys_forget_a_blocked_tile_after_a_while() -> None:
    sim = Sim([walk(100, 104)], Position(100, 100, 7), walk_keys=True)
    sim.bot.start()
    sim.step()
    for _ in range(_BLOCK_STEPS):
        sim.step()
    assert pressed(sim)[-1] == "left"
    sim.pos = Position(99, 100, 7)
    sim.step(13.0)  # the NPC left meanwhile
    sim.pos = Position(100, 100, 7)
    sim.step()
    assert pressed(sim)[-1] == "down"  # straight through again


#: Creatures on all 8 tiles around the player (slot (7, 5)).
_RING = [(6, 4), (7, 4), (8, 4), (6, 5), (8, 5), (6, 6), (7, 6), (8, 6)]


def _creature_sim(monkeypatch: pytest.MonkeyPatch, slots: list[tuple[int, int]]) -> Sim:
    """Keys mode, waypoint below, *slots* show up after the first step and block it."""
    seen: list[tuple[int, int]] = []
    monkeypatch.setattr("moonlight.cavebot.cavebot.find_creatures", lambda _f, _g: seen)
    sim = Sim(
        [walk(100, 104)],
        Position(100, 100, 7),
        walk_keys=True,
        frames=lambda: np.zeros((1, 1, 3), np.uint8),
    )
    sim.bot.start()
    sim.step()
    assert pressed(sim) == ["down"]  # nothing seen yet
    seen.extend(slots)
    for _ in range(_BLOCK_STEPS):  # the step down did nothing: blocked
        sim.step()
    return sim


def test_walk_keys_avoid_creatures_after_a_block(monkeypatch: pytest.MonkeyPatch) -> None:
    sim = _creature_sim(monkeypatch, [(6, 5)])  # a creature left of the player
    assert pressed(sim)[-1] == "right"


def test_walk_keys_step_diagonally_past_creatures(monkeypatch: pytest.MonkeyPatch) -> None:
    sim = _creature_sim(monkeypatch, [(6, 5), (8, 5), (7, 4), (7, 6)])  # only the free corners
    assert pressed(sim)[-1] in ("z", "c")


def test_walk_keys_stuck_without_seeing_creatures() -> None:
    sim = Sim([walk(100, 104)], Position(100, 100, 7), walk_keys=True)
    sim.state.monsters = [Monster("Rat", 100.0)]
    sim.bot.start()
    for _ in range(400):  # every tile around bumped, the scan saw nothing
        sim.step()
        if sim.bot.stuck_on_creatures:
            break
    assert sim.bot.stuck_on_creatures
    assert len(set(pressed(sim))) == 8  # tried all 8 tiles first


def test_walk_keys_stuck_when_creatures_close_every_way(monkeypatch: pytest.MonkeyPatch) -> None:
    sim = _creature_sim(monkeypatch, _RING)
    assert pressed(sim)[-1] in ("left", "right")  # nothing to fight: a tile not bumped
    assert not sim.bot.stuck_on_creatures
    sim.state.monsters = [Monster("Rat", 100.0)]
    for _ in range(_BLOCK_STEPS):
        sim.step()
    assert sim.bot.stuck_on_creatures  # the loop's targeting takes over
    n = len(pressed(sim))
    sim.state.selected_target = sim.state.monsters[0]
    for _ in range(_BLOCK_STEPS):  # fighting: no key
        sim.step()
    sim.state.selected_target = None
    sim.bot.stuck_on_creatures = False  # the loop clears it after a kill
    for _ in range(_BLOCK_STEPS):  # the way is still closed: stuck again, still no key
        sim.step()
    assert sim.bot.stuck_on_creatures
    assert len(pressed(sim)) == n
    slots = [(6, 5)]  # the others died: the tile below is free again
    monkeypatch.setattr("moonlight.cavebot.cavebot.find_creatures", lambda _f, _g: slots)
    for _ in range(_BLOCK_STEPS):
        sim.step()
    assert not sim.bot.stuck_on_creatures
    assert pressed(sim)[n:][:1] == ["down"]


def test_walk_keys_try_unbumped_tiles_with_an_empty_battle_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _creature_sim(monkeypatch, _RING)  # the scan says creatures close every way
    assert pressed(sim)[-1] in ("left", "right")  # not the bumped tile below
    for _ in range(_BLOCK_STEPS):  # that tile is blocked too: the other side next
        sim.step()
    assert set(pressed(sim)[2:]) == {"left", "right"}  # [1] pressed down again
    assert not sim.bot.stuck_on_creatures


def test_walk_keys_fight_through_instead_of_a_long_detour(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tile = np.full((256, 256, 3), (0, 255, 255), np.uint8)
    tile[100:105, 100] = 100  # a corridor down to the waypoint
    tile[100, 100:121] = tile[100:105, 120] = tile[104, 100:121] = 100  # a long loop around
    cv2.imwrite(str(tmp_path / "0_0_7.png"), tile)
    monkeypatch.setattr("moonlight.cavebot.cavebot.find_creatures", lambda _f, _g: [(7, 6)])
    sim = Sim(
        [walk(100, 104)],
        Position(100, 100, 7),
        walk_keys=True,
        frames=lambda: np.zeros((1, 1, 3), np.uint8),
        cost_map=CostMap(tmp_path),
    )
    sim.state.monsters = [Monster("Rat", 100.0)]
    sim.bot.start()
    for _ in range(_BLOCK_STEPS + 1):  # the step down is blocked by the rat below
        sim.step()
    assert pressed(sim) == ["down", "down"]  # a lag? pressed twice
    assert sim.bot.stuck_on_creatures


def _seeing_sim(monkeypatch: pytest.MonkeyPatch, slots: list[tuple[int, int]]) -> Sim:
    """Keys mode, waypoint below, the scan sees *slots* from the first tick, Rats listed."""
    monkeypatch.setattr("moonlight.cavebot.cavebot.find_creatures", lambda _f, _g: slots)
    sim = Sim(
        [walk(100, 104)],
        Position(100, 100, 7),
        walk_keys=True,
        frames=lambda: np.zeros((1, 1, 3), np.uint8),
    )
    sim.state.monsters = [Monster("Rat", 100.0)]
    sim.bot.start()
    sim.step()
    return sim


def test_walk_keys_walk_around_a_creature_ahead_without_bumping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _seeing_sim(monkeypatch, [(7, 6)])  # a rat on the tile below
    assert pressed(sim) in (["left"], ["right"], ["z"], ["c"])  # around it, not "down"
    assert not sim.bot.stuck_on_creatures


def test_walk_keys_walk_into_closing_creatures_before_standing_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = _seeing_sim(monkeypatch, _RING)  # no way around, but no bump yet
    assert pressed(sim) == ["down"]  # straight in: the scan can be a tile off
    assert not sim.bot.stuck_on_creatures
    for _ in range(_BLOCK_STEPS):  # it bumped: now targeting takes over
        sim.step()
    assert sim.bot.stuck_on_creatures


def test_walk_keys_stand_still_while_a_target_is_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    sim = _creature_sim(monkeypatch, [(6, 5)])  # the way around on the right is open
    sim.state.monsters = [Monster("Rat", 100.0)]
    sim.state.selected_target = sim.state.monsters[0]
    n = len(pressed(sim))
    for hp in range(90, 0, -10):  # 9 s fight: no arrow, no stuck alarm
        sim.state.selected_target = Monster("Rat", float(hp))
        sim.step(1.0)
    assert pressed(sim)[n:] == []
    sim.state.selected_target = None  # it died: walk on
    sim.step(1.0)
    assert pressed(sim)[n:] == ["right"]  # the bumped tile below is still blocked


def test_walk_keys_ignore_the_scrolling_view_right_after_a_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slots = [(6, 5)]  # a creature left of the player, the waypoint further left
    monkeypatch.setattr("moonlight.cavebot.cavebot.find_creatures", lambda _f, _g: slots)
    sim = Sim(
        [walk(96, 100)],
        Position(100, 100, 7),
        walk_keys=True,
        frames=lambda: np.zeros((1, 1, 3), np.uint8),
    )
    sim.bot.start()
    sim.step()
    for _ in range(_BLOCK_STEPS):
        sim.step()
    around = pressed(sim)[-1]
    assert around in ("down", "up")
    sim.pos = Position(100, 101 if around == "down" else 99, 7)
    sim.step()  # the view still shows the creature on its old screen slot: a tile off
    assert pressed(sim)[-1] == "left"  # not back to the blocked tile's row


def test_walk_keys_skip_unreachable_waypoints_until_one_has_a_way(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    tile = np.full((256, 256, 3), 100, np.uint8)
    tile[99:102, 109:112] = (0, 255, 255)  # walls all around waypoint 0
    tile[100, 110] = 100
    cv2.imwrite(str(tmp_path / "0_0_7.png"), tile)
    sim = Sim(
        [walk(110, 100), walk(100, 104, z=6), walk(100, 104)],
        Position(100, 100, 7),
        walk_keys=True,
        cost_map=CostMap(tmp_path),
    )
    sim.bot.start()
    with caplog.at_level("INFO", logger="moonlight.cavebot.cavebot"):
        for _ in range(3 * _BLOCK_STEPS):  # walks into the wall first: the map can be wrong
            sim.step()
            if sim.route.current == 2:
                break
    for _ in range(_BLOCK_STEPS // 2):
        sim.step()
    assert sim.route.current == 2  # waypoint 1 is on another floor: skipped too
    assert "waypoint 0 unreachable, skipping" in caplog.text
    assert "waypoint 1 unreachable, skipping" in caplog.text
    assert pressed(sim)[0] == "right" and pressed(sim)[-1] == "down"
    assert sim.bot.is_active()


def test_walk_keys_stuck_alarm_when_no_waypoint_is_reachable() -> None:
    sim = Sim([walk(100, 104)], Position(100, 100, 7), walk_keys=True)
    sim.bot.start()
    for _ in range(400):  # every tile around bumped, nothing to fight
        sim.step()
        if not sim.bot.is_active():
            break
    assert sim.bot.alarm == "stuck" and sim.now < 20
    assert len(set(pressed(sim))) == 8
