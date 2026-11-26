"""Floor change rules: the bot stops on a wrong floor, the check flags unconfirmed
floor changes, the path finder does not pair a yellow sqm with another's.

# doc: docs/prs/floor-rules.md
"""

from __future__ import annotations

from pathlib import Path

from moonlight.cavebot.cavebot import Route, Waypoint
from moonlight.cavebot.floor_changes import load_confirmed, record_confirmed
from moonlight.cavebot.route_plan import FloorMap, known_floor_kinds, route_warnings
from moonlight.core.state import Position
from tests.test_cavebot import Sim, walk
from tests.test_route_plan import _map


def test_stairs_that_lead_to_an_unexpected_floor_stop_the_route() -> None:
    # Thais 2026-11-26: a sewer grate taken for stairs up led down into the sewers.
    sim = Sim([Waypoint(100, 100, 7, "stairs"), walk(100, 95, 6)], Position(100, 101, 7))
    sim.bot.start()
    sim.step()
    sim.pos = Position(100, 100, 8)  # went down, the route expects floor 6 next
    sim.step()
    sim.step()
    assert sim.bot.alarm == "wrong_floor" and not sim.bot.is_active()


def test_stairs_to_the_expected_floor_go_on() -> None:
    sim = Sim([Waypoint(100, 100, 7, "stairs"), walk(100, 95, 6)], Position(100, 101, 7))
    sim.bot.start()
    sim.step()
    sim.pos = Position(100, 100, 6)
    sim.step()
    sim.step()
    assert sim.bot.alarm is None and sim.bot.is_active() and sim.route.current == 1


def test_a_floor_left_without_a_floor_change_waypoint_is_still_resynced() -> None:
    # Pushed down a hole in a fight: not the route's own floor change, no stop.
    sim = Sim([walk(100, 95, 7), walk(100, 90, 8)], Position(100, 100, 7))
    sim.bot.start()
    sim.step()
    sim.pos = Position(100, 100, 8)
    sim.step()
    sim.step()
    assert sim.bot.alarm != "wrong_floor" and sim.bot.is_active()


def test_a_wrong_floor_long_after_the_floor_change_is_resynced() -> None:
    sim = Sim(
        [Waypoint(100, 100, 7, "stairs"), walk(100, 95, 6), walk(100, 90, 6)],
        Position(100, 101, 7),
    )
    sim.bot.start()
    sim.step()
    sim.pos = Position(100, 100, 6)
    sim.step()
    sim.step(10.0)  # walked on floor 6 for a while
    sim.pos = Position(100, 97, 7)  # then a fight pushed it back down
    sim.step()
    assert sim.bot.alarm != "wrong_floor" and sim.bot.is_active()


# -- confirmed floor changes ----------------------------------------------------


def test_a_floor_change_that_lands_where_expected_is_recorded_once(tmp_path: Path) -> None:
    sim = Sim([Waypoint(100, 100, 7, "stairs"), walk(100, 95, 6)], Position(100, 101, 7))
    sim.bot.start()
    sim.step()
    sim.pos = Position(100, 100, 6)
    sim.step()
    sim.step()
    assert load_confirmed() == {((100, 100, 7), "stairs"): 6}
    assert not record_confirmed((100, 100, 7), "stairs", 6)  # already listed


def test_a_wrong_floor_is_not_recorded() -> None:
    sim = Sim([Waypoint(100, 100, 7, "stairs"), walk(100, 95, 6)], Position(100, 101, 7))
    sim.bot.start()
    sim.step()
    sim.pos = Position(100, 100, 8)
    sim.step()
    sim.step()
    assert load_confirmed() == {}


def test_check_names_floor_changes_not_confirmed_in_the_game(tmp_path: Path) -> None:
    route = Route("r", "", [walk(10, 10, 7), Waypoint(12, 10, 7, "ladder"), walk(12, 10, 6)])
    cost = _map(tmp_path, {})
    confirmed: dict[tuple[tuple[int, int, int], str], int] = {}
    assert any(
        "ladder at (12, 10, 7) not confirmed" in w for w in route_warnings(route, cost, confirmed)
    )
    confirmed[(12, 10, 7), "ladder"] = 6
    assert not any("not confirmed" in w for w in route_warnings(route, cost, confirmed))


def test_path_finder_takes_only_confirmed_floor_changes_as_known() -> None:
    record_confirmed((10, 10, 7), "stairs", 8)
    record_confirmed((20, 20, 7), "step", 8)
    assert known_floor_kinds() == {(10, 10, 7): "stairs"}  # a step sqm is not the hole


# -- pairing --------------------------------------------------------------------


def test_a_yellow_sqm_is_not_paired_with_the_end_of_other_stairs(tmp_path: Path) -> None:
    # Thais 2026-11-26: a grate 2 sqm from stairs was paired with the stairs' far end.
    floors = FloorMap(_map(tmp_path, {7: [(10, 10), (12, 10)], 8: [(10, 10)]}), {})
    assert [c.to[2] for c in floors.changes_at(10, 10, 7)] == [8]
    assert floors.changes_at(12, 10, 7) == ()  # the yellow sqm on 8 is the stairs'


def test_a_confirmed_floor_change_only_goes_the_confirmed_way(tmp_path: Path) -> None:
    cost = _map(tmp_path, {7: [(10, 10)], 8: [(10, 10)]})
    assert [c.to[2] for c in FloorMap(cost, {}).changes_at(10, 10, 7)] == [8]
    up = FloorMap(cost, {(10, 10, 7): "stairs"}, known_to={(10, 10, 7): 6})
    assert up.changes_at(10, 10, 7) == ()  # confirmed up: never paired down
