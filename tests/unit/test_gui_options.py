"""T0.F3.K1: tab options write the right BotConfig (no window opened)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from moonlight.gui.tabs.base import Option, read_option, write_option
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
