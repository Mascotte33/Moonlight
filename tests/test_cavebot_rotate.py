"""Offline tests for ``cavebot_rotate``: the range in a route name, the pick, the skip flag."""

# doc: docs/prs/cavebot-rotate.md#cavebot-rotate

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from moonlight.cavebot.cavebot import Cavebot, Route
from moonlight.scripts.cavebot_rotate import (
    CavebotRotateError,
    pick_route,
    route_level_range,
    run,
)
from moonlight.scripts.scripts import ScriptRunner


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("VenoreSwampTrolls 20-40", (20, 40)),
        ("Two Words 8-8", (8, 8)),
        ("VenoreSwampTrolls", None),
        ("Trolls 20-", None),
        ("Trolls20-40", None),  # no space before the range
        ("Trolls 20-40 old", None),  # the range is trailing
        ("Trolls 40-20", None),  # min over max
    ],
)
def test_the_level_range_is_the_trailing_min_max(
    name: str, expected: tuple[int, int] | None
) -> None:
    assert route_level_range(name) == expected


def write_routes(folder: Path, *names: str) -> None:
    for name in names:
        waypoint = {"x": 1, "y": 2, "z": 7, "action": "walk"}
        (folder / f"{name}.json").write_text(
            json.dumps({"name": name, "waypoints": [waypoint]}), encoding="utf-8"
        )


def first(names: list[str]) -> str:
    return names[0]


def last(names: list[str]) -> str:
    return names[-1]


def test_only_routes_whose_range_holds_the_level_are_picked(tmp_path: Path) -> None:
    write_routes(tmp_path, "A 20-40", "B 40-60", "C 41-60", "No range")
    assert pick_route(tmp_path, 40, first) == "A 20-40"
    assert pick_route(tmp_path, 40, last) == "B 40-60"  # both ends included
    assert pick_route(tmp_path, 50, first) == "B 40-60"


def test_a_level_no_route_fits_is_an_error_naming_it(tmp_path: Path) -> None:
    write_routes(tmp_path, "A 20-40", "No range")
    with pytest.raises(CavebotRotateError, match="level 99"):
        pick_route(tmp_path, 99)


class FakeCavebot:
    def __init__(self, route_name: str) -> None:
        self.route = Route(name=route_name)
        self.switched_to: list[str] = []
        self.alarm: str | None = None

    def switch_route(self, route: Route) -> None:
        self.route = route
        self.switched_to.append(route.name)

    def stop_with_alarm(self, alarm: str) -> None:
        self.alarm = alarm


def runner_for(tmp_path: Path, level: int | None, current: str) -> tuple[ScriptRunner, FakeCavebot]:
    runner = ScriptRunner(tmp_path)
    runner.routes_dir = tmp_path
    runner.level = lambda: level
    cavebot = FakeCavebot(current)
    runner.cavebot = cast(Cavebot, cavebot)
    return runner, cavebot


def test_a_new_route_is_loaded_and_the_next_run_skips(tmp_path: Path) -> None:
    write_routes(tmp_path, "A 20-40", "B 20-40")
    runner, cavebot = runner_for(tmp_path, 30, "A 20-40")
    list(run(runner, last))
    assert cavebot.switched_to == ["B 20-40"] and runner.skip_cavebot_rotate
    list(run(runner, first))  # the new route's own rotate: skipped once
    assert cavebot.switched_to == ["B 20-40"] and not runner.skip_cavebot_rotate
    list(run(runner, first))  # the one after switches again
    assert cavebot.switched_to == ["B 20-40", "A 20-40"]


def test_the_current_route_picked_again_is_kept(tmp_path: Path) -> None:
    write_routes(tmp_path, "A 20-40", "B 20-40")
    runner, cavebot = runner_for(tmp_path, 30, "A 20-40")
    list(run(runner, first))
    assert cavebot.switched_to == [] and not runner.skip_cavebot_rotate and cavebot.alarm is None


def test_an_account_switch_skips_once(tmp_path: Path) -> None:
    write_routes(tmp_path, "A 20-40", "B 20-40")
    runner, cavebot = runner_for(tmp_path, 30, "A 20-40")
    runner.skip_cavebot_rotate = True  # what the account switch sets
    list(run(runner, last))
    assert cavebot.switched_to == [] and not runner.skip_cavebot_rotate
    list(run(runner, last))
    assert cavebot.switched_to == ["B 20-40"]


def test_an_unread_level_stops_the_route(tmp_path: Path) -> None:
    write_routes(tmp_path, "A 20-40")
    runner, cavebot = runner_for(tmp_path, None, "X")
    list(run(runner, first))
    assert cavebot.alarm is not None and "Skills window" in cavebot.alarm
    assert cavebot.switched_to == []


def test_a_level_without_a_route_stops_the_route(tmp_path: Path) -> None:
    write_routes(tmp_path, "A 20-40")
    runner, cavebot = runner_for(tmp_path, 99, "A 20-40")
    list(run(runner, first))
    assert cavebot.alarm is not None and "level 99" in cavebot.alarm


def test_it_is_a_built_in_script(tmp_path: Path) -> None:
    runner = ScriptRunner(tmp_path)  # no cavebot_rotate.py in the folder
    assert runner.start("cavebot_rotate") and runner.running == "cavebot_rotate"
