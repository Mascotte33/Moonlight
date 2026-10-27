"""T0.F1.K3 and K5: named configs in configs/ (no window opened)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from moonlight.core.config import DEFAULT_CONFIG_FILE, BotConfig, ConfigError
from moonlight.core.state import WorldState
from moonlight.gui.loop_factory import (
    config_names,
    delete_config,
    load_real_config,
    pick_config,
    picked_config,
    save_config_as,
)
from moonlight.gui.state_bridge import GuiContext, HardwareController, LogFeed


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_first_run_creates_default_from_defaults() -> None:
    assert picked_config() == Path("configs/default.json")
    assert BotConfig.load("configs/default.json") == BotConfig.default()


def test_first_run_copies_moonlight_config() -> None:
    replace(BotConfig.default(), simulation=True).save(DEFAULT_CONFIG_FILE)
    assert load_real_config().simulation is True


def test_save_as_writes_named_file_and_picks_it() -> None:
    cfg = replace(BotConfig.default(), serial_baudrate=9600)
    path = save_config_as(cfg, "rot-worms_2")
    assert path == Path("configs/rot-worms_2.json")
    assert json.loads(path.read_text())["serial_baudrate"] == 9600
    assert picked_config() == path


def test_save_as_asks_before_overwrite() -> None:
    save_config_as(BotConfig.default(), "a")
    with pytest.raises(FileExistsError):
        save_config_as(BotConfig.default(), "a")
    save_config_as(replace(BotConfig.default(), simulation=True), "a", overwrite=True)
    assert load_real_config().simulation is True


@pytest.mark.parametrize("name", ["", "a b", "../x", "x.json", "ż"])
def test_bad_name_refused(name: str) -> None:
    with pytest.raises(ConfigError):
        save_config_as(BotConfig.default(), name)


def test_dropdown_lists_the_files() -> None:
    for name in ("b", "a"):
        save_config_as(BotConfig.default(), name)
    assert config_names() == ["a", "b"]


def test_last_pick_is_restored() -> None:
    save_config_as(BotConfig.default(), "a")
    save_config_as(replace(BotConfig.default(), simulation=True), "b")
    pick_config("a")
    assert Path("configs/last.txt").read_text() == "a"
    assert picked_config() == Path("configs/a.json")
    assert load_real_config().simulation is False


def test_delete_removes_file_and_picks_another() -> None:
    save_config_as(BotConfig.default(), "a")
    save_config_as(BotConfig.default(), "b")
    assert delete_config("b") == Path("configs/a.json")
    assert config_names() == ["a"]
    assert picked_config() == Path("configs/a.json")


def test_delete_keeps_the_pick_when_another_goes() -> None:
    save_config_as(BotConfig.default(), "a")
    save_config_as(BotConfig.default(), "b")
    assert delete_config("a") == Path("configs/b.json")


def test_only_config_cannot_be_deleted() -> None:
    save_config_as(BotConfig.default(), "a")
    with pytest.raises(ConfigError):
        delete_config("a")
    assert config_names() == ["a"]


def test_config_changed_reaches_the_app() -> None:
    calls: list[bool] = []
    ctx = GuiContext(
        state=WorldState(),
        hardware=HardwareController(),
        log=LogFeed(),
    )
    ctx.config_changed()  # no app hooked: nothing happens
    ctx.on_config_change = calls.append
    ctx.config_changed()
    ctx.config_changed(restart=True)
    assert calls == [False, True]


def test_bot_runs_the_cavebot_tabs_waypoint_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from moonlight.cavebot.cavebot import RouteFileLoader, Waypoint
    from moonlight.cavebot.recorder import save_route
    from moonlight.core.config import CavebotConfig
    from moonlight.gui import loop_factory

    routes = Path("data/waypoints")
    routes.mkdir(parents=True)
    saved = RouteFileLoader.load(save_route(routes, "r", [Waypoint(1, 1, 7)]))
    replace(BotConfig.default(), cavebot=CavebotConfig(route=saved)).save(picked_config())
    shown = [Waypoint(1, 1, 7), Waypoint(2, 2, 7)]
    monkeypatch.setattr(loop_factory, "editor_route", lambda: ("r", shown))
    cavebot = loop_factory.gui_bot_config().cavebot
    assert cavebot is not None and cavebot.route.waypoints == shown
    monkeypatch.setattr(loop_factory, "editor_route", lambda: ("other", shown))
    cavebot = loop_factory.gui_bot_config().cavebot
    assert cavebot is not None and cavebot.route.waypoints == [Waypoint(1, 1, 7)]
