"""T4.F1.K1: Healing tab options and spell hotkeys (no window opened)."""

from __future__ import annotations

import pytest

from moonlight.gui.tabs.healing import apply_healing
from moonlight.setup.config import BotConfig, ConfigError

VALUES: dict[str, object] = {
    "healing.emergency_hp_percent": 25.0,
    "healing.normal_hp_percent": 65.0,
    "healing.mana_threshold": 30.0,
    "healing.heal_cooldown_ms": 750.0,
    "healing.emergency_spell": "exura vita",
    "healing.normal_spell": "exura",
    "healing.cure_spell": "exana pox",
}
COMBOS = {
    "healing.emergency_spell": "F3",
    "healing.normal_spell": "F2",
    "healing.cure_spell": "F4",
}


def test_apply_writes_thresholds_and_hotkeys() -> None:
    cfg = apply_healing(BotConfig.default(), VALUES, COMBOS)
    h = cfg.healing
    assert h is not None
    assert (h.emergency_hp_percent, h.normal_hp_percent) == (25.0, 65.0)
    assert (h.mana_threshold, h.heal_cooldown_ms) == (30.0, 750)
    assert h.emergency_spell == "exura vita"
    assert cfg.spell_combos == {
        "exura gran": "F1",
        "exura": "F2",
        "exura vita": "F3",
        "exana pox": "F4",
    }


def test_spell_with_no_hotkey_is_refused() -> None:
    with pytest.raises(ConfigError, match="exura vita"):
        apply_healing(BotConfig.default(), VALUES, {**COMBOS, "healing.emergency_spell": " "})
