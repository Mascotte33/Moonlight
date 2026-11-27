"""One setting, one place: the key lists come from BotConfig's fields."""

from __future__ import annotations

from dataclasses import fields, replace

from moonlight.core import config
from moonlight.core.config import BotConfig, LiveConfig
from moonlight.scripts.scripts import ScriptRunner


def test_every_plain_field_loads_and_machine_fields_are_tagged() -> None:
    names = {f.name for f in fields(BotConfig)}
    assert set(config._SCALAR_KEYS) <= names
    assert set(config.MACHINE_KEYS) <= names
    assert "cavebot_rotate" in config.MACHINE_KEYS and "min_level" not in config.MACHINE_KEYS
    assert {"loop", "clicker", "modules"} <= config._TOP_KEYS


def test_a_scalar_field_round_trips_through_from_dict() -> None:
    cfg = replace(BotConfig.default(), cavebot_rotate=False, min_level=15)
    back = BotConfig.from_dict(cfg.to_dict())
    assert back.cavebot_rotate is False and back.min_level == 15


def test_the_runner_reads_the_live_settings() -> None:
    runner = ScriptRunner()
    assert runner.cavebot_rotate_on
    live = LiveConfig(BotConfig())
    runner.settings = live
    live.current = replace(live.current, cavebot_rotate=False)
    assert not runner.cavebot_rotate_on
