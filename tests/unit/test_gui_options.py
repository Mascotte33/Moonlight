"""T0.F3.K1: tab options write the right BotConfig (no window opened)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from moonlight.gui.tabs.base import Option, combo_from_key, read_option, write_option
from moonlight.setup.config import BotConfig, ConfigError

HP = Option("Heal at HP %", "healing.normal_hp_percent", "slider", min=0, max=100)


def test_dotted_write_sets_the_field() -> None:
    cfg = write_option(BotConfig.default(), HP, 65.0)
    assert cfg.healing is not None
    assert cfg.healing.normal_hp_percent == 65.0
    assert cfg.targeting == BotConfig.default().targeting


def test_top_level_and_nested_fields() -> None:
    cfg = write_option(BotConfig.default(), Option("Sim", "simulation", "switch"), True)
    assert cfg.simulation is True
    cfg = write_option(cfg, Option("Baud", "serial_baudrate", "entry"), "9600")
    assert cfg.serial_baudrate == 9600


def test_slider_float_fits_an_int_field() -> None:
    opt = Option("Cooldown", "healing.heal_cooldown_ms", "slider", min=0, max=2000)
    cfg = write_option(BotConfig.default(), opt, 499.6)
    assert cfg.healing is not None and cfg.healing.heal_cooldown_ms == 500


def test_write_into_none_section_creates_it() -> None:
    off = replace(BotConfig.default(), healing=None)
    cfg = write_option(off, HP, 65.0)
    # The value lands in the section; the module stays off until its switch.
    assert cfg.healing is None
    assert read_option(cfg, HP.field) == 65.0
    on = write_option(cfg, Option("Healing", "healing.enabled", "switch"), True)
    assert on.healing is not None and on.healing.normal_hp_percent == 65.0


def test_enabled_switch_off_keeps_values() -> None:
    cfg = write_option(BotConfig.default(), HP, 65.0)
    cfg = write_option(cfg, Option("Healing", "healing.enabled", "switch"), False)
    assert cfg.healing is None
    assert read_option(cfg, "healing.enabled") is False
    assert read_option(cfg, HP.field) == 65.0


@pytest.mark.parametrize("value", [-1.0, 101.0])
def test_out_of_range_refused(value: float) -> None:
    with pytest.raises(ConfigError):
        write_option(BotConfig.default(), HP, value)


def test_unknown_field_and_bad_number_refused() -> None:
    with pytest.raises(ConfigError):
        write_option(BotConfig.default(), Option("X", "healing.nope", "entry"), "1")
    with pytest.raises(ConfigError):
        read_option(BotConfig.default(), "clicker.nope")
    with pytest.raises(ConfigError):
        write_option(BotConfig.default(), Option("Baud", "serial_baudrate", "entry"), "fast")


def test_blank_entry_writes_none_into_optional_field() -> None:
    port = Option("Port", "serial_port", "entry")
    cfg = write_option(BotConfig.default(), port, "COM3")
    assert cfg.serial_port == "COM3"
    assert write_option(cfg, port, "").serial_port is None


def test_tick_rate_option_writes_loop_tick_rate() -> None:
    from moonlight.gui.tabs.settings import OPTIONS

    (tick,) = [o for o in OPTIONS if o.field == "loop.tick_rate"]
    cfg = write_option(BotConfig.default(), tick, 30.0)
    assert cfg.loop.tick_rate == 30.0
    with pytest.raises(ConfigError):
        write_option(cfg, tick, 61.0)


def _switch(name: str) -> Option:
    from moonlight.gui.tabs.settings import MODULE_SWITCHES

    (option,) = [o for o in MODULE_SWITCHES if o.field == f"{name}.enabled"]
    return option


def test_looting_switch_off_and_on_keeps_values() -> None:
    cfg = replace(BotConfig.default(), monster_name_source=True)
    cfg = write_option(cfg, _switch("looting"), True)
    cfg = write_option(cfg, Option("Max", "looting.max_items_per_loot", "entry"), "7")
    cfg = write_option(cfg, _switch("looting"), False)
    assert cfg.looting is None
    cfg = write_option(cfg, _switch("looting"), True)
    assert cfg.looting is not None and cfg.looting.max_items_per_loot == 7


def test_cavebot_switch_without_route_refused() -> None:
    with pytest.raises(ConfigError, match="route"):
        write_option(BotConfig.default(), _switch("cavebot"), True)


def test_max_monsters_writes_targeting_field() -> None:
    from moonlight.gui.tabs.targeting import OPTIONS

    opt = next(o for o in OPTIONS if o.field == "targeting.max_monsters_to_engage")
    cfg = write_option(BotConfig.default(), opt, 7.0)
    assert cfg.targeting is not None and cfg.targeting.max_monsters_to_engage == 7
    with pytest.raises(ConfigError):
        write_option(cfg, opt, 11)


def test_off_module_without_kept_values_shows_defaults() -> None:
    cfg = BotConfig.default()
    assert cfg.attack_spells is None and "attack_spells" not in cfg.off
    assert read_option(cfg, "attack_spells.aoe_min_monsters") == 3
    assert read_option(cfg, "cavebot.rope_hotkey") is None


def test_no_field_is_shown_by_two_tabs() -> None:
    from moonlight.gui import tabs

    fields = [o.field for name in tabs.__all__ for o in getattr(tabs, name).options]
    assert sorted(f for f in set(fields) if fields.count(f) > 1) == []


@pytest.mark.parametrize(
    ("cfg", "name", "reason"),
    [
        (BotConfig.default(), "cavebot", "Cavebot needs a route"),
        (BotConfig.default(), "looting", "Looting needs a monster-name reader"),
        (BotConfig.default(), "buffs", "Buffs needs a buff-icon reader"),
        (BotConfig.default(), "refill", "Refill needs Cavebot and Looting on"),
        (replace(BotConfig.default(), hotkeys={}), "healing", "Set a hotkey for each spell"),
    ],
)
def test_refused_switch_has_a_plain_reason(cfg: BotConfig, name: str, reason: str) -> None:
    from moonlight.gui.tabs.settings import plain_reason

    cfg = replace(cfg, healing=None) if name == "healing" else cfg
    with pytest.raises(ConfigError) as exc:
        write_option(cfg, _switch(name), True)
    assert plain_reason(str(exc.value)).startswith(reason)


def test_refill_without_capacity_reader_has_a_plain_reason() -> None:
    from moonlight.gui.tabs.settings import plain_reason

    msg = "refill is enabled but no capacity perception source is configured"
    assert plain_reason(msg).startswith("Refill needs a capacity reader")
    assert plain_reason("something else") == "something else"


@pytest.mark.parametrize(
    ("text", "value"), [("25", 25), ("24.6", 25), ("99", 60), ("0", 1), ("abc", 10), ("", 10)]
)
def test_typed_slider_value_is_a_whole_number(text: str, value: int) -> None:
    from moonlight.gui.tabs.base import typed_value

    assert typed_value(text, 1, 60, fallback=10) == value


def test_whole_number_fits_a_float_field() -> None:
    cfg = write_option(BotConfig.default(), HP, 65)
    assert cfg.healing is not None and cfg.healing.normal_hp_percent == 65


def test_remove_drops_the_selected_creatures() -> None:
    from moonlight.gui.tabs.targeting import remove_creatures

    names = ["Rat", "Cave Rat", "Troll"]
    assert remove_creatures(names, (0, 2), "") == ["Cave Rat"]
    assert remove_creatures(names, (), "troll") == ["Rat", "Cave Rat"]


@pytest.mark.parametrize(
    ("keysym", "keycode", "state", "combo"),
    [
        ("F1", 0x70, 0, "F1"),
        ("F2", 0x71, 0x4, "ctrl+F2"),
        ("A", 0x41, 0x1, "shift+a"),
        ("exclam", 0x31, 0x1, "shift+1"),
        ("Next", 0x22, 0x20000, "alt+pgdn"),
        ("space", 0x20, 0, "space"),
    ],
)
def test_a_key_press_becomes_a_device_combo(
    keysym: str, keycode: int, state: int, combo: str
) -> None:
    assert combo_from_key(keysym, keycode, state) == combo


@pytest.mark.parametrize("keysym", ["Control_L", "Shift_R", "Alt_L", "Caps_Lock"])
def test_a_lone_modifier_is_no_combo(keysym: str) -> None:
    assert combo_from_key(keysym, 0x11, 0x4) is None
