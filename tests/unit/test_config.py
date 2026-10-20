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
    # F-09: looting / refill need their perception sources wired (T34).
    data["monster_name_source"] = True
    data["capacity_source"] = True
    path = tmp_path / "moonlight_config.json"
    path.write_text(json.dumps(data))
    cfg = BotConfig.load(path)
    assert cfg.healing == HealingConfig(normal_hp_percent=65.0)
    assert cfg.cavebot is not None and cfg.cavebot.route.name == "hunt"
    assert cfg.cavebot.rope_hotkey == "F5"
    assert cfg.refill is not None and cfg.refill.trigger_cap == 80.0
    assert cfg.loop.tick_rate == 10.0 and cfg.serial_port == "COM7"
    assert cfg.targeting is None
    assert cfg.monster_name_source is True and cfg.capacity_source is True


def test_looting_refused_without_name_source() -> None:
    """F-09: looting without a monster-name perception source is refused."""
    data = raw(looting={"enabled": True})
    with pytest.raises(ConfigError, match="monster-name perception source"):
        BotConfig.from_dict(data)
    data["monster_name_source"] = True
    assert BotConfig.from_dict(data).looting is not None


def test_refill_refused_without_capacity_source(tmp_path: Path) -> None:
    """F-09: refill without a capacity perception source is refused."""
    route_file(tmp_path / "routes")
    data = raw(
        cavebot={"enabled": True, "route": "hunt"},
        looting={"enabled": True},
        refill={"enabled": True},
    )
    data["routes_dir"] = str(tmp_path / "routes")
    # Let the looting guard pass so the capacity guard is what fires.
    data["monster_name_source"] = True
    with pytest.raises(ConfigError, match="capacity perception source"):
        BotConfig.from_dict(data)
    data["capacity_source"] = True
    assert BotConfig.from_dict(data).refill is not None


def test_buffs_refused_without_icon_source() -> None:
    """F-10: buffs without buff-icon perception is refused."""
    data = raw(buffs={"enabled": True})
    with pytest.raises(ConfigError, match="buff-icon perception source"):
        BotConfig.from_dict(data)
    data["buff_icon_source"] = True
    assert BotConfig.from_dict(data).buffs is not None


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
        # F-03: minimap_start must be a (x, y, z) of non-negative ints.
        lambda d: d.update(minimap_start=[100, 200]),
        lambda d: d.update(minimap_start=[-1, 0, 0]),
        lambda d: d.update(minimap_start=[True, 0, 0]),
        lambda d: d.update(minimap_start=["abc", 0, 0]),
        lambda d: d.update(minimap_start="not-a-tuple"),
    ],
)
def test_fail_closed(mutate: Any) -> None:
    data = raw()
    mutate(data)
    with pytest.raises(ConfigError):
        BotConfig.from_dict(data)


def test_minimap_start_roundtrip() -> None:
    """F-03: a valid minimap_start round-trips as an absolute (x, y, z) seed."""
    data = raw()
    data["minimap_start"] = [100, 200, 7]
    cfg = BotConfig.from_dict(data)
    assert cfg.minimap_start == (100, 200, 7)
    assert cfg.validate() is cfg


def test_minimap_start_default_none() -> None:
    """F-03: minimap_start defaults to None (legacy relative behavior)."""
    assert BotConfig.default().minimap_start is None


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
