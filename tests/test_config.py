"""T40: BotConfig -- defaults, JSON load, validation, fail closed."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from moonlight.core.config import MODULES, BotConfig, ConfigError
from moonlight.healing.healing import CureRule, HealingConfig, HealingRule
from moonlight.refill.refill import RefillConfig
from moonlight.targeting.targeting import AreaRule


def route_file(d: Path, name: str = "hunt") -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(
        json.dumps({"name": name, "waypoints": [{"x": 100, "y": 100, "z": 7}]})
    )


def raw(**modules: dict[str, Any]) -> dict[str, Any]:
    """Minimal valid config; *modules* override sections."""
    sections: dict[str, Any] = {name: {"enabled": False} for name in MODULES}
    sections.update(modules)
    return {"profile": "p", "hotkeys": {}, "modules": sections}


def test_old_walk_keys_in_cavebot_still_loads(tmp_path: Path) -> None:
    route_file(tmp_path / "routes")
    data = raw(cavebot={"enabled": True, "route": "hunt", "walk_keys": True})
    data["routes_dir"] = str(tmp_path / "routes")
    path = tmp_path / "moonlight_config.json"
    path.write_text(json.dumps(data))
    assert BotConfig.load(path).walk_keys is True


def test_default_is_valid() -> None:
    cfg = BotConfig.default()
    assert cfg.healing is not None and cfg.targeting is not None and cfg.alarms is not None
    assert cfg.buffs is None and cfg.cavebot is None and cfg.refill is None
    assert cfg.hotkeys == {}


def test_load_roundtrip(tmp_path: Path) -> None:
    route_file(tmp_path / "routes")
    data = raw(
        healing={"enabled": True, "spell_cooldown_ms": 800},
        cavebot={"enabled": True, "route": "hunt", "rope_hotkey": "F5"},
        looting={"enabled": True},
        refill={"enabled": True, "trigger_cap": 80.0, "cap_below": 300},
    )
    data["routes_dir"] = str(tmp_path / "routes")
    data["loop"] = {"tick_rate": 10.0, "enable_f12_monitor": False}
    data["serial_port"] = "COM7"
    data["walk_keys"] = True
    # F-09: looting / refill need their perception sources wired (T34).
    data["monster_name_source"] = True
    data["capacity_source"] = True
    path = tmp_path / "moonlight_config.json"
    path.write_text(json.dumps(data))
    cfg = BotConfig.load(path)
    assert cfg.healing == HealingConfig(spell_cooldown_ms=800)
    assert cfg.cavebot is not None and cfg.cavebot.route.name == "hunt"
    assert cfg.cavebot.rope_hotkey == "F5" and cfg.walk_keys is True
    assert cfg.refill is not None and cfg.refill.cap_below == 300  # the old used cap % is dropped
    assert cfg.loop.tick_rate == 10.0 and cfg.serial_port == "COM7"
    assert cfg.targeting is None
    assert cfg.monster_name_source is True and cfg.capacity_source is True


def test_looting_loads_a_config_saved_with_the_loot_window_keys() -> None:
    data = raw(
        looting={
            "enabled": True,
            "move_all_hotkey": None,
            "max_items_per_loot": 25,
            "min_cap_to_loot": 10.0,
        }
    )
    assert BotConfig.from_dict(data).looting is not None


def test_refill_follows_the_cavebot_and_loads_old_route_file_keys(tmp_path: Path) -> None:
    route_file(tmp_path / "routes")
    old = {"enabled": False, "route_name": "refill", "stop_cap": 50.0, "deposit_hotkey": "F5"}
    assert BotConfig.from_dict(raw(refill={**old, "enabled": True})).refill is None
    data = raw(cavebot={"enabled": True, "route": "hunt"}, refill=old)
    data["routes_dir"] = str(tmp_path / "routes")
    assert BotConfig.from_dict(data).refill == RefillConfig()


def test_scripts_timers_load_and_a_config_without_scripts_still_loads() -> None:
    cfg = BotConfig.from_dict(raw(scripts={"enabled": True, "timers": "eat 60"}))
    assert cfg.scripts is not None and cfg.scripts.timers == "eat 60"
    old = raw()
    del old["modules"]["scripts"]
    assert BotConfig.from_dict(old).scripts is None
    with pytest.raises(ConfigError):
        BotConfig.from_dict(raw(scripts={"enabled": True, "timers": "eat"}))


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
        lambda d: d["modules"].update(healing={"enabled": True, "spell_cooldown_ms": "800"}),
        lambda d: d["modules"].update(healing={"enabled": True, "spell_cooldown_ms": 0}),
        # P2: an old healing section is refused, not migrated.
        lambda d: d["modules"].update(healing={"enabled": True, "emergency_spell": "exura gran"}),
        lambda d: d["modules"].update(
            healing={"enabled": True, "rules": [{"kind": "spell", "hotkey": "F1"}]}
        ),
        lambda d: d["modules"].update(targeting={"enabled": True, "max_monsters_to_engage": True}),
        lambda d: d.update(loop={"tick_rate": -1.0}),
        lambda d: d.update(hotkeys={"exura": "F 2"}),
        lambda d: d.update(profile=""),
        lambda d: d["modules"].update(cavebot={"enabled": True}),
        lambda d: d["modules"].update(cavebot={"enabled": True, "route": "missing"}),
    ],
)
def test_fail_closed(mutate: Any) -> None:
    data = raw()
    mutate(data)
    with pytest.raises(ConfigError):
        BotConfig.from_dict(data)


def test_old_minimap_start_is_ignored() -> None:
    data = raw()
    data["minimap_start"] = [100, 200, 7]
    assert BotConfig.from_dict(data) == BotConfig.from_dict(raw())


def test_clicker_open_loop_switch_and_validation() -> None:
    """F-14: open-loop clicking is a config switch; bad values fail closed."""
    assert BotConfig.default().clicker.open_loop is False
    data = raw()
    data["clicker"] = {"open_loop": True, "mouse_scale": 1.5, "settle_ms": 25}
    cfg = BotConfig.from_dict(data)
    assert cfg.clicker.open_loop and cfg.clicker.mouse_scale == 1.5
    data["clicker"] = {"settle_ms": 500}
    with pytest.raises(ConfigError):
        BotConfig.from_dict(data)


def test_attack_spells_section_rejected() -> None:
    data = raw(attack_spells={"enabled": False})
    with pytest.raises(ConfigError, match="attack_spells"):
        BotConfig.from_dict(data)


def test_area_rules_load_from_json() -> None:
    data = raw(
        targeting={
            "enabled": True,
            "area_rules": [
                {"hotkey": "F5", "minimum_adjacent_monsters": 3, "minimum_mana_percent": 30}
            ],
            "attack_cooldown_ms": 1500,
        }
    )
    targeting = BotConfig.from_dict(data).targeting
    assert targeting is not None
    assert targeting.area_rules == (AreaRule("F5", 3, 30.0),)
    assert targeting.attack_cooldown_ms == 1500


@pytest.mark.parametrize(
    "targeting",
    [
        {"area_rules": [{"hotkey": "F5", "minimum_adjacent_monsters": 0}]},
        {"area_rules": [{"hotkey": "", "minimum_adjacent_monsters": 2}]},
        {"area_rules": [{"hotkey": "F5", "minimum_mana_percent": 120}]},
        {"area_rules": [{"minimum_adjacent_monsters": 2}]},
        {"attack_cooldown_ms": -1},
        {"default_monster": {"minimum_mana_percent": -5}},
    ],
)
def test_bad_attack_settings_rejected(targeting: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        BotConfig.from_dict(raw(targeting={"enabled": True, **targeting}))


def test_healing_rules_and_cures_load_from_json_lists() -> None:
    data = raw(
        healing={
            "enabled": True,
            "rules": [{"kind": "potion", "hotkey": "F3", "hp_below_percent": 40}],
            "cures": [{"condition": "poison", "enabled": True, "hotkey": "shift+F1"}],
        }
    )
    healing = BotConfig.from_dict(data).healing
    assert healing is not None
    assert healing.rules == (HealingRule("potion", "F3", hp_below_percent=40.0),)
    assert healing.cures == (CureRule("poison", True, "shift+F1"),)


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


def test_to_dict_roundtrip_default() -> None:
    cfg = BotConfig.default()
    assert BotConfig.from_dict(cfg.to_dict()) == cfg


def test_save_roundtrip_every_section(tmp_path: Path) -> None:
    route_file(tmp_path / "routes")
    data = raw(
        healing={
            "enabled": True,
            "rules": [{"kind": "spell", "hotkey": "F1", "mp_below_percent": 20}],
            "cures": [{"condition": "burn", "enabled": True, "hotkey": "F9"}],
        },
        targeting={"enabled": True},
        buffs={
            "enabled": True,
            "buffs": [{"buff_name": "haste", "spell": "utani hur", "priority": 1}],
        },
        alarms={"enabled": True},
        cavebot={"enabled": True, "route": "hunt", "rope_hotkey": "F5"},
        looting={"enabled": True},
        refill={"enabled": True, "cap_below": 300},
    )
    data["hotkeys"]["utani hur"] = "F3"
    data.update(
        serial_port="COM7",
        profiles_dir=str(tmp_path / "profiles"),
        routes_dir=str(tmp_path / "routes"),
        loop={"tick_rate": 10.0},
        clicker={"open_loop": True},
        monster_name_source=True,
        capacity_source=True,
        buff_icon_source=True,
    )
    cfg = BotConfig.from_dict(data)
    path = tmp_path / "saved.json"
    cfg.save(path)
    assert BotConfig.load(path) == cfg


def test_off_module_keeps_its_values(tmp_path: Path) -> None:
    """T0.F1.K4: an off module's values round-trip, and the bot sees it off."""
    data = raw(healing={"enabled": False, "spell_cooldown_ms": 800})
    cfg = BotConfig.from_dict(data)
    assert cfg.healing is None
    assert cfg.off["healing"] == HealingConfig(spell_cooldown_ms=800)
    path = tmp_path / "saved.json"
    cfg.save(path)
    assert json.loads(path.read_text())["modules"]["healing"]["spell_cooldown_ms"] == 800
    assert BotConfig.load(path) == cfg


def test_off_entry_for_enabled_module_refused() -> None:
    cfg = BotConfig.default()
    with pytest.raises(ConfigError, match="healing"):
        replace(cfg, off={"healing": HealingConfig()}).validate()


def test_window_title_roundtrip() -> None:
    from moonlight.perception.capture import TIBIA_WINDOW_TITLE

    assert BotConfig.default().window_title == TIBIA_WINDOW_TITLE
    cfg = replace(BotConfig.default(), window_title="Tibia - Gunzodus")
    assert BotConfig.from_dict(cfg.to_dict()).window_title == "Tibia - Gunzodus"


def test_extras_load_and_a_config_without_extras_still_loads() -> None:
    cfg = BotConfig.from_dict(raw(extras={"enabled": True, "food": True, "food_hotkey": "F10"}))
    assert cfg.extras is not None and cfg.extras.food_hotkey == "F10"
    old = raw()
    del old["modules"]["extras"]
    assert BotConfig.from_dict(old).extras is None
    with pytest.raises(ConfigError):
        BotConfig.from_dict(raw(extras={"enabled": True, "food": True}))


def test_rotation_accounts_load_round_trip_and_need_the_cavebot(tmp_path: Path) -> None:
    route_file(tmp_path / "routes")
    accounts = [{"login": "a1", "password": "p1"}, {"login": "b2", "password": "p2", "minutes": 30}]
    section = {"enabled": True, "accounts": accounts}
    with pytest.raises(ConfigError, match="rotation needs the cavebot"):
        BotConfig.from_dict(raw(rotation=section))
    data = raw(cavebot={"enabled": True, "route": "hunt"}, rotation=section)
    data["routes_dir"] = str(tmp_path / "routes")
    cfg = BotConfig.from_dict(data)
    assert cfg.rotation is not None and cfg.rotation.accounts[1].minutes == 30.0
    assert BotConfig.from_dict(cfg.to_dict()) == cfg
    old = raw()
    del old["modules"]["rotation"]
    assert BotConfig.from_dict(old).rotation is None
    data["modules"]["rotation"] = {"enabled": True, "accounts": accounts[:1]}
    with pytest.raises(ConfigError, match="needs at least 2 accounts"):
        BotConfig.from_dict(data)
    off = BotConfig.from_dict(raw(rotation={"enabled": False, "logout_hotkey": "ctrl+l"}))
    assert off.off["rotation"].accounts == ()  # no accounts yet is fine while off
