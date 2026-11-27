"""Floor change rules: the bot stops on a wrong floor, the check flags unconfirmed
floor changes, the path finder does not pair a yellow sqm with another's.

# doc: docs/prs/floor-rules.md
"""

from __future__ import annotations

from pathlib import Path

import pytest

from moonlight.cavebot.cavebot import Route, Waypoint
from moonlight.cavebot.floor_changes import load_confirmed, record_confirmed
from moonlight.cavebot.route_plan import FloorMap, known_floor_kinds, route_warnings
from moonlight.core.state import Position
from tests.script_modules import attach
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


# -- depot floors ---------------------------------------------------------------


class _FakeBot:
    def __init__(self, position: tuple[int, int, int], labels: set[str]) -> None:
        self.position, self._labels, self.went = position, labels, ""

    def has_label(self, label: str) -> bool:
        return label in self._labels

    def goto(self, label: str) -> list[object]:
        self.went = label
        return []


def _go_to_city(bot: _FakeBot) -> str:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "go_to_city", Path(__file__).parents[1] / "scripts" / "go_to_city.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    list(module.run(bot))
    return bot.went


def test_go_to_city_takes_the_block_of_the_depot_floor_the_character_is_on() -> None:
    labels = {"FromThais", "FromThaisFloor8"}
    assert _go_to_city(_FakeBot((32346, 32220, 7), labels)) == "FromThais"
    assert _go_to_city(_FakeBot((32346, 32222, 8), labels)) == "FromThaisFloor8"
    assert _go_to_city(_FakeBot((32346, 32222, 8), {"FromThais"})) == "FromThais"  # no such block


def test_bot_has_label_reads_the_running_route() -> None:
    from moonlight.scripts.scripts import Bot, ScriptRunner

    runner = ScriptRunner()
    assert not Bot(runner).has_label("FromThais")  # no cavebot
    sim = Sim([Waypoint(0, 0, 0, "label", "FromThais"), walk(100, 95, 7)], Position(100, 100, 7))
    attach(runner, cavebot=sim.bot)
    assert Bot(runner).has_label("FromThais") and not Bot(runner).has_label("FromCarlin")


def test_recorded_hole_is_never_walked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hole the map does not show, listed as kind ``hole``, counts as a floor change."""
    from moonlight.cavebot import floor_changes
    from moonlight.cavebot.pathing import CostMap

    csv = tmp_path / "floor_changes.csv"
    csv.write_text("x,y,z,kind,to_z\n32697,31950,7,hole,8\n", encoding="utf-8")
    monkeypatch.setattr(floor_changes, "FLOOR_CHANGES_FILE", csv)
    cost = CostMap(tmp_path / "cost")
    assert cost.floor_change(32697, 31950, 7)
    assert not cost.floor_change(32698, 31950, 7)
