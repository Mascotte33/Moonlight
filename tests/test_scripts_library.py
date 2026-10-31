"""The Scripts tab's logic: new, rename and delete keep the routes in step."""

# doc: docs/prs/scripts-page.md#scripts-tab

from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonlight.cavebot.cavebot import Route, Waypoint
from moonlight.cavebot.tab import BROKEN_MARK, renamed_script, waypoint_table
from moonlight.core.config import MODULES, BotConfig, ConfigError
from moonlight.scripts.library import (
    NEW_SCRIPT,
    delete_script,
    name_error,
    new_script,
    rename_script,
    routes_using,
    script_names,
)
from moonlight.scripts.scripts import ScriptsConfig, script_error
from moonlight.scripts.tab import with_scripts


def route(routes: Path, name: str, *labels: str) -> None:
    routes.mkdir(exist_ok=True)
    waypoints = [{"x": 1, "y": 1, "z": 7}] + [
        {"x": 0, "y": 0, "z": 0, "action": "script", "label": label} for label in labels
    ]
    (routes / f"{name}.json").write_text(json.dumps({"name": name, "waypoints": waypoints}))


def labels(routes: Path, name: str) -> list[str]:
    raw = json.loads((routes / f"{name}.json").read_text())
    return [wp["label"] for wp in raw["waypoints"] if wp.get("action") == "script"]


def test_new_writes_a_working_template(tmp_path: Path) -> None:
    new_script(tmp_path, "eat")
    assert script_names(tmp_path) == ["eat"]
    assert (tmp_path / "eat.py").read_text() == NEW_SCRIPT
    assert script_error(NEW_SCRIPT) is None
    assert name_error(tmp_path, "eat") == "eat already exists"
    with pytest.raises(ValueError):
        new_script(tmp_path, "my script")


def test_rename_follows_the_waypoints_in_every_route(tmp_path: Path) -> None:
    scripts, routes = tmp_path / "scripts", tmp_path / "routes"
    new_script(scripts, "eat")
    route(routes, "a", "eat", "rope")
    route(routes, "b", "eat")
    assert routes_using(routes, "eat") == 2
    rename_script(scripts, routes, "eat", "food")
    assert script_names(scripts) == ["food"]
    assert labels(routes, "a") == ["food", "rope"] and labels(routes, "b") == ["food"]


def test_delete_drops_its_waypoints(tmp_path: Path) -> None:
    scripts, routes = tmp_path / "scripts", tmp_path / "routes"
    new_script(scripts, "eat")
    route(routes, "a", "eat", "rope")
    delete_script(scripts, routes, "eat")
    assert script_names(scripts) == [] and labels(routes, "a") == ["rope"]


def test_the_route_editor_marks_and_follows_scripts() -> None:
    wps = [Waypoint(1, 1, 7), Waypoint(0, 0, 0, "script", "eat"), Waypoint(0, 0, 0, "say", "eat")]
    table = waypoint_table(Route("r", waypoints=wps), runs_badly=lambda s: s == "eat")
    assert [line.endswith(BROKEN_MARK) for line in table.splitlines()] == [
        False, False, True, False,
    ]  # fmt: skip
    assert [wp.label for wp in renamed_script(wps, "eat", "food")] == ["", "food", "eat"]
    assert [wp.action for wp in renamed_script(wps, "eat", None)] == ["walk", "say"]


def test_with_scripts_writes_the_section_checked() -> None:
    cfg = BotConfig.from_dict({"profile": "p", "modules": {n: {"enabled": False} for n in MODULES}})
    scripts = ScriptsConfig(("eat",), {"eat": 500})
    assert with_scripts(cfg, scripts).scripts == scripts
    with pytest.raises(ConfigError):
        with_scripts(cfg, ScriptsConfig(pause_ms={"eat": 10}))
