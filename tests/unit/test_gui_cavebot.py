"""Cavebot tab route picker and waypoint table (T3.F1.K1)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from moonlight.decision.cavebot import RouteFileLoader
from moonlight.gui.tabs.cavebot import (
    apply_cavebot,
    picked_route,
    route_names,
    waypoint_line,
    waypoint_table,
)
from moonlight.setup.config import BotConfig, ConfigError


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
    values: dict[str, object] = {"cavebot.route": "rotworms", "cavebot.rope_hotkey": "F7"}
    cfg = apply_cavebot(cfg, values)
    assert cfg.off["cavebot"].rope_hotkey == "F7"
    assert picked_route(cfg) == "rotworms"


def test_blank_hotkey_keeps_default(tmp_path: Path) -> None:
    cfg = replace(BotConfig.default(), routes_dir=_routes(tmp_path))
    cfg = apply_cavebot(cfg, {"cavebot.route": "rotworms", "cavebot.shovel_hotkey": ""})
    assert cfg.off["cavebot"].shovel_hotkey == "F10"


def test_no_route_on_disk_changes_nothing(tmp_path: Path) -> None:
    cfg = replace(BotConfig.default(), routes_dir=_routes(tmp_path))
    assert apply_cavebot(cfg, {"cavebot.route": "No routes"}) == cfg


def test_table_rows_come_from_the_file(tmp_path: Path) -> None:
    route = RouteFileLoader.load(_routes(tmp_path) / "rotworms.json")
    lines = waypoint_table(route).splitlines()
    assert lines[1].split() == ["1", "walk", "100", "200", "7", "START"]
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
    cfg = apply_cavebot(cfg, {"cavebot.route": "rotworms", "cavebot.start_index": "2"})
    assert cfg.off["cavebot"].start_index == 1


def test_blank_deposit_hotkey_writes_none(tmp_path: Path) -> None:
    cfg = replace(BotConfig.default(), routes_dir=_routes(tmp_path))
    cfg = apply_cavebot(cfg, {"refill.deposit_hotkey": "F5"})
    assert cfg.off["refill"].deposit_hotkey == "F5"
    cfg = apply_cavebot(cfg, {"refill.deposit_hotkey": ""})
    assert cfg.off["refill"].deposit_hotkey is None


def test_loot_values_are_written(tmp_path: Path) -> None:
    cfg = replace(BotConfig.default(), routes_dir=_routes(tmp_path))
    cfg = apply_cavebot(cfg, {"looting.move_all_hotkey": "", "looting.min_cap_to_loot": "5"})
    kept = cfg.off["looting"]
    assert (kept.move_all_hotkey, kept.min_cap_to_loot) == (None, 5.0)


def test_loot_tab_has_no_unused_max_items() -> None:
    from moonlight.gui.tabs.cavebot import LOOT_OPTIONS

    assert "looting.max_items_per_loot" not in [o.field for o in LOOT_OPTIONS]


def test_switch_on_with_picked_route(tmp_path: Path) -> None:
    cfg = replace(BotConfig.default(), routes_dir=_routes(tmp_path))
    cfg = apply_cavebot(cfg, {"cavebot.route": "rotworms", "cavebot.enabled": True})
    assert cfg.cavebot is not None and picked_route(cfg) == "rotworms"


def test_switch_on_without_route_refused(tmp_path: Path) -> None:
    cfg = replace(BotConfig.default(), routes_dir=_routes(tmp_path))
    with pytest.raises(ConfigError, match="cavebot.route"):
        apply_cavebot(cfg, {"cavebot.route": "Pick a route", "cavebot.enabled": True})


def test_every_waypoint_type_has_a_button() -> None:
    from moonlight.decision.cavebot import ACTIONS
    from moonlight.gui.tabs.cavebot import KIND_ROWS

    assert sum(KIND_ROWS, ()) == ACTIONS
