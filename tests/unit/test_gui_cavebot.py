"""Cavebot tab route picker and waypoint table (T3.F1.K1)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from moonlight.bot.state import Position
from moonlight.decision.cavebot import RouteFileLoader
from moonlight.gui.tabs.cavebot import (
    add_waypoint,
    apply_cavebot,
    picked_route,
    route_names,
    waypoint_line,
    waypoint_table,
)
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


def test_current_waypoint_is_marked(tmp_path: Path) -> None:
    route = RouteFileLoader.load(_routes(tmp_path) / "rotworms.json")
    lines = waypoint_table(route, current=1).splitlines()
    assert lines[2].startswith(">")
    assert not lines[1].startswith(">")
    assert ">" not in waypoint_table(route)


def test_waypoint_line() -> None:
    assert waypoint_line(1, 2) == "Waypoint 2 of 2"
    assert waypoint_line(None, 2) == "Not running"


def test_live_route_is_none_without_a_running_bot() -> None:
    from types import SimpleNamespace

    from moonlight.gui.state_bridge import GuiContext

    ctx = SimpleNamespace(controller=None, bot_running=lambda: False)
    assert GuiContext.live_route(ctx) is None  # type: ignore[arg-type]
    cavebot = SimpleNamespace(route="r")
    loop = SimpleNamespace(modules=SimpleNamespace(cavebot=cavebot))
    ctx = SimpleNamespace(controller=SimpleNamespace(loop=loop), bot_running=lambda: True)
    assert GuiContext.live_route(ctx) == "r"  # type: ignore[arg-type]


def test_start_waypoint_is_given_one_based(tmp_path: Path) -> None:
    cfg = replace(BotConfig.default(), routes_dir=_routes(tmp_path))
    cfg = apply_cavebot(cfg, {"cavebot.enabled": True, "cavebot.route": "rotworms",
                              "cavebot.start_index": "2"})
    assert cfg.cavebot is not None and cfg.cavebot.start_index == 1


def test_add_waypoint_stores_the_live_position(tmp_path: Path) -> None:
    path = _routes(tmp_path) / "rotworms.json"
    assert add_waypoint(path, Position(105, 210, 8)) == "Added 105, 210, 8"
    wp = RouteFileLoader.load(path).waypoints[-1]
    assert (wp.x, wp.y, wp.z, wp.action) == (105, 210, 8, "walk")


def test_unknown_position_adds_nothing(tmp_path: Path) -> None:
    path = _routes(tmp_path) / "rotworms.json"
    before = path.read_text(encoding="utf-8")
    assert add_waypoint(path, Position(0, 0, 0)) == "Position unknown"
    assert path.read_text(encoding="utf-8") == before


def test_blank_deposit_hotkey_writes_none(tmp_path: Path) -> None:
    cfg = replace(BotConfig.default(), routes_dir=_routes(tmp_path))
    cfg = apply_cavebot(cfg, {"refill.deposit_hotkey": "F5"})
    assert cfg.off["refill"].deposit_hotkey == "F5"
    cfg = apply_cavebot(cfg, {"refill.deposit_hotkey": ""})
    assert cfg.off["refill"].deposit_hotkey is None


def test_loot_values_are_written(tmp_path: Path) -> None:
    cfg = replace(BotConfig.default(), routes_dir=_routes(tmp_path))
    cfg = apply_cavebot(cfg, {"looting.max_items_per_loot": 12.0,
                              "looting.move_all_hotkey": "", "looting.min_cap_to_loot": "5"})
    kept = cfg.off["looting"]
    assert (kept.max_items_per_loot, kept.move_all_hotkey, kept.min_cap_to_loot) == (12, None, 5.0)
