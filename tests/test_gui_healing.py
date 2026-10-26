"""Healing tab options: the two slot cooldowns (no window opened)."""

from __future__ import annotations

from moonlight.core.config import BotConfig
from moonlight.gui.tab_base import write_option
from moonlight.healing.tab import OPTIONS


def test_cooldown_sliders_write_the_slot_cooldowns() -> None:
    assert [o.field for o in OPTIONS] == ["healing.spell_cooldown_ms", "healing.potion_cooldown_ms"]
    cfg = BotConfig.default()
    for option, value in zip(OPTIONS, (750.0, 400.0), strict=True):
        cfg = write_option(cfg, option, value)
    assert cfg.healing is not None
    assert (cfg.healing.spell_cooldown_ms, cfg.healing.potion_cooldown_ms) == (750, 400)
