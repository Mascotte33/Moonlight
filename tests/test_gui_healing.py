"""Healing tab: the row-to-config step and the cooldown options (no window opened)."""

from __future__ import annotations

import pytest

from moonlight.core.config import BotConfig, ConfigError
from moonlight.gui.tab_base import Option, write_option
from moonlight.healing.healing import CONDITIONS
from moonlight.healing.tab import OPTIONS, CureRow, RuleRow, apply_healing

CURES = [CureRow(name, False, "") for name in CONDITIONS]


def test_cooldown_sliders_write_the_slot_cooldowns() -> None:
    assert [o.field for o in OPTIONS] == ["healing.spell_cooldown_ms", "healing.potion_cooldown_ms"]
    cfg = BotConfig.default()
    for option, value in zip(OPTIONS, (750.0, 400.0), strict=True):
        cfg = write_option(cfg, option, value)
    assert cfg.healing is not None
    assert (cfg.healing.spell_cooldown_ms, cfg.healing.potion_cooldown_ms) == (750, 400)


def test_rows_become_rules_in_order_and_cures() -> None:
    rules = [RuleRow("potion", "F4", "", "30", "0"), RuleRow("spell", "F1", "85", "", "20")]
    cures = [CureRow("poison", True, "shift+F1"), *CURES[1:]]
    cfg = apply_healing(BotConfig.default(), rules, cures, 900, 600)
    healing = cfg.healing
    assert healing is not None
    assert [
        (r.kind, r.hotkey, r.hp_below_percent, r.mp_below_percent, r.minimum_mana_percent)
        for r in healing.rules
    ] == [
        ("potion", "F4", 0.0, 30.0, 0.0),
        ("spell", "F1", 85.0, 0.0, 20.0),
    ]
    assert [(c.condition, c.enabled, c.hotkey) for c in healing.cures] == [
        ("poison", True, "shift+F1")
    ]
    assert (healing.spell_cooldown_ms, healing.potion_cooldown_ms) == (900, 600)


def test_a_turned_off_healing_module_stays_off() -> None:
    cfg = write_option(BotConfig.default(), Option("on", "healing.enabled", "switch"), False)
    cfg = apply_healing(cfg, [RuleRow("spell", "F1", "50", "", "")], CURES, 1000, 1000)
    assert cfg.healing is None
    assert cfg.off["healing"].rules[0].hotkey == "F1"


@pytest.mark.parametrize(
    ("rules", "cures"),
    [
        ([RuleRow("spell", "F1", "abc", "", "")], CURES),  # not a number
        ([RuleRow("spell", "F1", "", "", "")], CURES),  # no trigger
        ([RuleRow("spell", "", "50", "", "")], CURES),  # no hotkey
        ([RuleRow("spell", "F1", "150", "", "")], CURES),  # out of range
        ([RuleRow("spell", "F1", "50", "", "")], [CureRow("poison", True, "")]),  # cure no key
    ],
)
def test_a_refused_row_raises_config_error(rules: list[RuleRow], cures: list[CureRow]) -> None:
    with pytest.raises(ConfigError):
        apply_healing(BotConfig.default(), rules, cures, 1000, 1000)
