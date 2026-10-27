"""Cavebot tab route picker and waypoint table (T3.F1.K1)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from moonlight.cavebot.cavebot import RouteFileLoader
from moonlight.cavebot.tab import (
    apply_cavebot,
    drop_route,
    picked_route,
    picked_waypoint,
    route_names,
    waypoint_line,
    waypoint_table,
)
from moonlight.core.config import BotConfig, ConfigError


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
    assert lines[1].split() == ["1", "walk", "(100,", "200,", "7)"]
    assert lines[2].split() == ["2", "rope", "(101,", "200,", "7)"]
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


def test_live_cavebot_is_none_without_a_running_bot() -> None:
    from types import SimpleNamespace

    from moonlight.gui.state_bridge import GuiContext

    ctx = SimpleNamespace(controller=None, bot_running=lambda: False)
    assert GuiContext.live_cavebot(ctx) is None  # type: ignore[arg-type]
    cavebot = SimpleNamespace(route="r")
    loop = SimpleNamespace(modules=SimpleNamespace(cavebot=cavebot))
    ctx = SimpleNamespace(controller=SimpleNamespace(loop=loop), bot_running=lambda: True)
    assert GuiContext.live_cavebot(ctx) is cavebot  # type: ignore[arg-type]


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
    cfg = apply_cavebot(cfg, {"looting.wait_ms": "600", "looting.min_cap_to_loot": "5"})
    kept = cfg.off["looting"]
    assert (kept.wait_ms, kept.min_cap_to_loot) == (600, 5.0)


def test_loot_tab_has_no_unused_max_items() -> None:
    from moonlight.cavebot.tab import LOOT_OPTIONS

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
    from moonlight.cavebot.cavebot import ACTIONS
    from moonlight.cavebot.tab import KIND_ROWS

    assert sum(KIND_ROWS, ()) == tuple(a for a in ACTIONS if a != "stairs")


def test_direction_pad_is_every_neighbour_and_the_middle() -> None:
    from moonlight.cavebot.tab import DIRECTIONS

    offsets = [(dx, dy) for _, dx, dy in DIRECTIONS]
    assert offsets == [(dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
    assert dict((a, (dx, dy)) for a, dx, dy in DIRECTIONS)["↑"] == (0, -1)


def test_dropping_the_picked_route_moves_the_cavebot(tmp_path: Path) -> None:
    routes = _routes(tmp_path)
    orcs = (routes / "rotworms.json").read_text(encoding="utf-8").replace("rotworms", "orcs")
    (routes / "orcs.json").write_text(orcs, encoding="utf-8")
    cfg = replace(BotConfig.default(), routes_dir=routes)
    cfg = apply_cavebot(cfg, {"cavebot.route": "rotworms", "cavebot.enabled": True})
    assert drop_route(cfg, "orcs") == cfg
    moved = drop_route(cfg, "rotworms")
    assert moved.cavebot is not None and picked_route(moved) == "orcs"


def test_dropping_the_last_route_turns_the_cavebot_off(tmp_path: Path) -> None:
    cfg = replace(BotConfig.default(), routes_dir=_routes(tmp_path))
    cfg = apply_cavebot(cfg, {"cavebot.route": "rotworms", "cavebot.enabled": True})
    off = drop_route(cfg, "rotworms")
    assert off.cavebot is None and picked_route(off) == ""



def test_picked_waypoint_skips_the_header_and_blank_lines() -> None:
    assert picked_waypoint(1, 3) is None  # header
    assert picked_waypoint(2, 3) == 0
    assert picked_waypoint(4, 3) == 2
    assert picked_waypoint(5, 3) is None
