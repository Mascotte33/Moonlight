"""Unit tests for moonlight.decision.cavebot (T22).

Offline: a tiny simulated world (``Sim``) renders synthetic frames -- a
radar with the player marker at its center (x/y come from an injected
MapMatcher) and a floor indicator drawn from an injected TemplateSet.
Clicks go to a recording ClickController; hotkeys go to FakeInput.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from moonlight.clicker import ClickController
from moonlight.decision.cavebot import Cavebot, Route, RouteError, RouteFileLoader, Waypoint
from moonlight.input import ClickSide, FakeInput
from moonlight.loop import GameLoop, LoopConfig
from moonlight.perception.floor import FloorReader, TemplateSet
from moonlight.perception.minimap import (
    PLAYER_CENTER_X,
    PLAYER_CENTER_Y,
    PLAYER_MARKER_RGB,
    RADAR_HEIGHT,
    RADAR_WIDTH,
    MinimapReader,
)
from moonlight.state import Position, WorldState

MINIMAP = (10, 10, RADAR_WIDTH, RADAR_HEIGHT)
FLOOR = (150, 10, 24, 24)
GAME = (200, 0, 480, 352)  # 32 px tiles; player tile center = (440, 176)
FLOOR_TEMPLATES = {
    z: np.random.RandomState(z).randint(0, 255, (16, 16)).astype(np.uint8) for z in (6, 7, 8, 9)
}


class RecordingClicker(ClickController):
    """Records click_at calls; *on_click* lets a test move the world."""

    def __init__(self) -> None:
        self.clicks: list[tuple[tuple[int, int], ClickSide]] = []

    def click_at(
        self,
        target: tuple[int, int],
        *,
        tolerance: int = 2,
        max_iters: int = 5,
        side: ClickSide = ClickSide.LEFT,
    ) -> bool:
        self.clicks.append((target, side))
        return True


class Sim:
    """Player position + clock + the Cavebot wired to synthetic readers."""

    def __init__(self, waypoints: list[Waypoint], start: Position) -> None:
        self.pos: Position | None = start
        self.now = 0.0
        self.fake = FakeInput()
        self.clicker = RecordingClicker()
        self.state = WorldState(client_focused=True, screen_ok=True)
        self.reached: list[int] = []

        sim = self

        class Matcher:
            def match(self, radar_gray: np.ndarray) -> tuple[int, int] | None:
                return None if sim.pos is None else (sim.pos.x, sim.pos.y)

        self.route = Route("test", "", waypoints)
        self.bot = Cavebot(
            self.route,
            MinimapReader(minimap_region=MINIMAP, map_matcher=Matcher()),
            FloorReader(floor_region=FLOOR, template_set=TemplateSet(FLOOR_TEMPLATES)),
            self.clicker,
            self.state,
            frame_provider=self.frame,
            game_region=GAME,
            input_backend=self.fake,
            clock=lambda: self.now,
            on_waypoint_reached=self.reached.append,
        )

    def frame(self) -> np.ndarray:
        frame = np.zeros((200, 400, 3), dtype=np.uint8)
        if self.pos is not None:
            frame[MINIMAP[1] + PLAYER_CENTER_Y, MINIMAP[0] + PLAYER_CENTER_X] = PLAYER_MARKER_RGB
            tpl = FLOOR_TEMPLATES.get(self.pos.z)
            if tpl is not None:
                x, y = FLOOR[0] + 4, FLOOR[1] + 4
                frame[y : y + 16, x : x + 16] = tpl[:, :, None]
        return frame

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


def test_readme_example_is_valid() -> None:
    readme = Path(__file__).parents[2] / "data" / "waypoints" / "README.md"
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
        {"name": "r", "waypoints": [{"x": -1, "y": 1, "z": 7}]},
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
    reader = MinimapReader(minimap_region=MINIMAP)
    with pytest.raises(ValueError):
        Cavebot(
            Route("r", "", [Waypoint(1, 1, 7, "rope")]),
            reader,
            FloorReader(floor_region=FLOOR, template_set=TemplateSet(FLOOR_TEMPLATES)),
            RecordingClicker(),
            WorldState(),
            frame_provider=lambda: np.zeros((1, 1, 3), np.uint8),
            game_region=GAME,
        )


# -- navigation ----------------------------------------------------------------


def test_walk_clicks_minimap_toward_waypoint_then_completes() -> None:
    sim = Sim([walk(105, 98)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    center = (MINIMAP[0] + RADAR_WIDTH // 2, MINIMAP[1] + RADAR_HEIGHT // 2)
    assert sim.clicker.clicks == [((center[0] + 5, center[1] - 2), ClickSide.LEFT)]

    sim.pos = Position(104, 99, 7)  # within 1 tile counts as reached
    sim.step()
    assert sim.reached == [0]
    assert not sim.bot.is_active() and sim.bot.alarm is None


def test_far_waypoint_click_is_clamped_to_radar() -> None:
    sim = Sim([walk(1000, 100)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    (x, _y), _side = sim.clicker.clicks[0]
    assert MINIMAP[0] < x < MINIMAP[0] + RADAR_WIDTH


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
    for _ in range(90):  # 4.5 s standing still
        sim.step()
    assert sim.bot.is_active()
    for _ in range(20):
        sim.step()
    assert not sim.bot.is_active() and sim.bot.alarm == "stuck"
    clicks = len(sim.clicker.clicks)
    sim.step()
    assert len(sim.clicker.clicks) == clicks


def test_unreadable_position_sends_nothing_and_still_times_out() -> None:
    sim = Sim([walk(120, 100)], Position(100, 100, 7))
    sim.pos = None
    sim.bot.start()
    for _ in range(110):
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
    assert sim.clicker.clicks[-1][0][0] > MINIMAP[0] + RADAR_WIDTH // 2  # walking east


def test_floor_change_with_no_matching_waypoint_alarms_lost() -> None:
    sim = Sim([walk(110, 100), walk(120, 100)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    sim.pos = Position(100, 100, 9)
    sim.step()
    assert not sim.bot.is_active() and sim.bot.alarm == "lost"


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
