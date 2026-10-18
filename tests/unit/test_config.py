"""T40: BotConfig -- defaults, JSON load, validation, fail closed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from moonlight.config import MODULES, BotConfig, ConfigError
from moonlight.decision.healing import HealingConfig


def route_file(d: Path, name: str = "hunt") -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(
        json.dumps({"name": name, "waypoints": [{"x": 100, "y": 100, "z": 7}]})
    )


def raw(**modules: dict[str, Any]) -> dict[str, Any]:
    """Minimal valid config; *modules* override sections."""
    sections: dict[str, Any] = {name: {"enabled": False} for name in MODULES}
    sections.update(modules)
    return {"profile": "p", "hotkeys": {"exura gran": "F1", "exura": "F2"}, "modules": sections}


def test_default_is_valid() -> None:
    cfg = BotConfig.default()
    assert cfg.healing is not None and cfg.targeting is not None and cfg.alarms is not None
    assert cfg.buffs is None and cfg.cavebot is None and cfg.refill is None
    assert cfg.spell_combos["exura"] == "F2"


def test_load_roundtrip(tmp_path: Path) -> None:
    route_file(tmp_path / "routes")
    data = raw(
        healing={"enabled": True, "normal_hp_percent": 65},
        cavebot={"enabled": True, "route": "hunt", "rope_hotkey": "F5"},
        looting={"enabled": True},
        refill={"enabled": True, "trigger_cap": 80.0},
    )
    data["routes_dir"] = str(tmp_path / "routes")
    data["loop"] = {"tick_rate": 10.0, "enable_f12_monitor": False}
    data["serial_port"] = "COM7"
    path = tmp_path / "moonlight_config.json"
    path.write_text(json.dumps(data))
    cfg = BotConfig.load(path)
    assert cfg.healing == HealingConfig(normal_hp_percent=65.0)
    assert cfg.cavebot is not None and cfg.cavebot.route.name == "hunt"
    assert cfg.cavebot.rope_hotkey == "F5"
    assert cfg.refill is not None and cfg.refill.trigger_cap == 80.0
    assert cfg.loop.tick_rate == 10.0 and cfg.serial_port == "COM7"
    assert cfg.targeting is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("profile"),
        lambda d: d.pop("modules"),
        lambda d: d["modules"].pop("healing"),
        lambda d: d["modules"]["healing"].pop("enabled"),
        lambda d: d["modules"]["healing"].update(enabled="yes"),
        lambda d: d.update(bogus=1),
        lambda d: d["modules"].update(bogus={"enabled": False}),
        lambda d: d["modules"].update(healing={"enabled": True, "nope": 1}),
        lambda d: d["modules"].update(healing={"enabled": True, "normal_hp_percent": "70"}),
        lambda d: d["modules"].update(healing={"enabled": True, "normal_hp_percent": 10.0}),
        lambda d: d["modules"].update(targeting={"enabled": True, "max_monsters_to_engage": True}),
        lambda d: d.update(loop={"tick_rate": -1.0}),
        lambda d: d.update(hotkeys={"exura": "F 2"}),
        lambda d: d.update(profile=""),
        lambda d: d["modules"].update(cavebot={"enabled": True}),
        lambda d: d["modules"].update(cavebot={"enabled": True, "route": "missing"}),
        lambda d: d["modules"].update(refill={"enabled": True}),
    ],
)
def test_fail_closed(mutate: Any) -> None:
    data = raw()
    mutate(data)
    with pytest.raises(ConfigError):
        BotConfig.from_dict(data)


def test_spell_without_hotkey_rejected() -> None:
    data = raw(healing={"enabled": True})
    data["hotkeys"] = {"exura": "F2"}  # exura gran unmapped
    with pytest.raises(ConfigError, match="exura gran"):
        BotConfig.from_dict(data)


def test_load_missing_or_bad_json(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        BotConfig.load(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ConfigError):
        BotConfig.load(bad)
    top = tmp_path / "list.json"
    top.write_text("[]")
    with pytest.raises(ConfigError):
        BotConfig.load(top)
