"""Cavebot tab route picker and waypoint table (T3.F1.K1)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from moonlight.decision.cavebot import RouteFileLoader
from moonlight.gui.tabs.cavebot import apply_cavebot, picked_route, route_names, waypoint_table
from moonlight.setup.config import BotConfig


def _routes(tmp_path: Path) -> Path:
    d = tmp_path / "waypoints"
    d.mkdir()
    route = {
        "name": "rotworms",
        "waypoints": [{"x": 100, "y": 200, "z": 7}, {"x": 101, "y": 200, "z": 7, "action": "rope"}],
    }
    (d / "rotworms.json").write_text(json.dumps(route), encoding="utf-8")
    return d


def test_route_names(tmp_path: Path) -> None:
    assert route_names(_routes(tmp_path)) == ["rotworms"]


def test_picking_a_route_writes_cavebot_route(tmp_path: Path) -> None:
    cfg = replace(BotConfig.default(), routes_dir=_routes(tmp_path))
    values = {"cavebot.enabled": True, "cavebot.route": "rotworms", "cavebot.rope_hotkey": "F7"}
    cfg = apply_cavebot(cfg, values)
    assert cfg.cavebot is not None
    assert cfg.cavebot.rope_hotkey == "F7"
    assert picked_route(cfg) == "rotworms"


def test_blank_hotkey_keeps_default(tmp_path: Path) -> None:
    cfg = replace(BotConfig.default(), routes_dir=_routes(tmp_path))
    cfg = apply_cavebot(cfg, {"cavebot.route": "rotworms", "cavebot.shovel_hotkey": ""})
    assert cfg.off["cavebot"].shovel_hotkey == "F10"


def test_no_route_on_disk_changes_nothing(tmp_path: Path) -> None:
    cfg = replace(BotConfig.default(), routes_dir=_routes(tmp_path))
    assert apply_cavebot(cfg, {"cavebot.enabled": True, "cavebot.route": "No routes"}) == cfg


def test_table_rows_come_from_the_file(tmp_path: Path) -> None:
    route = RouteFileLoader.load(_routes(tmp_path) / "rotworms.json")
    lines = waypoint_table(route).splitlines()
    assert lines[1].split() == ["1", "walk", "100", "200", "7"]
    assert lines[2].split() == ["2", "rope", "101", "200", "7"]
    assert waypoint_table(None) == "No waypoints loaded"
