"""Targeting tab: the row-to-config step (no window opened)."""

from __future__ import annotations

import pytest

from moonlight.core.config import BotConfig, ConfigError
from moonlight.gui.tab_base import Option, write_option
from moonlight.targeting.tab import (
    MOVEMENT_LABELS,
    AreaRuleFields,
    MonsterRowFields,
    TargetingSettings,
    apply_targeting,
)
from moonlight.targeting.targeting import MOVEMENTS

DEFAULT_ROW = MonsterRowFields("", "1", False, "", "", "follow")
NAMED_ROW = MonsterRowFields("a", "1", False, "", "", "follow")
SETTINGS = TargetingSettings("1500", "6")


def test_rows_become_default_monster_named_rows_and_area_rules() -> None:
    monsters = [
        MonsterRowFields("", "2", False, "F5", "10", "none"),
        MonsterRowFields(" Troll ", "4", False, "F6", "", "none"),
        MonsterRowFields("Rat", "1", True, "", "", "follow"),
    ]
    areas = [AreaRuleFields("F7", "3", "25"), AreaRuleFields("F8", "1", "")]
    cfg = apply_targeting(BotConfig.default(), monsters, areas, SETTINGS)
    targeting = cfg.targeting
    assert targeting is not None
    assert (targeting.default_monster.priority, targeting.default_monster.hotkey) == (2, "F5")
    assert targeting.default_monster.minimum_mana_percent == 10.0
    assert [(r.name, r.priority, r.ignore, r.hotkey, r.movement) for r in targeting.monsters] == [
        ("Troll", 4, False, "F6", "none"),
        ("Rat", 1, True, "", "follow"),
    ]
    assert [
        (r.hotkey, r.minimum_adjacent_monsters, r.minimum_mana_percent)
        for r in targeting.area_rules
    ] == [
        ("F7", 3, 25.0),
        ("F8", 1, 0.0),
    ]
    assert (targeting.attack_cooldown_ms, targeting.unreachable_after_seconds) == (1500, 6.0)


def test_chase_keys_are_saved() -> None:
    settings = TargetingSettings("1500", "6", " F9 ", "F10")
    cfg = apply_targeting(BotConfig.default(), [DEFAULT_ROW], [], settings)
    assert cfg.targeting is not None
    assert (cfg.targeting.chase_hotkey, cfg.targeting.stand_still_hotkey) == ("F9", "F10")


def test_a_turned_off_targeting_module_stays_off() -> None:
    cfg = write_option(BotConfig.default(), Option("on", "targeting.enabled", "switch"), False)
    cfg = apply_targeting(cfg, [DEFAULT_ROW], [AreaRuleFields("F7", "2", "")], SETTINGS)
    assert cfg.targeting is None
    assert cfg.off["targeting"].area_rules[0].hotkey == "F7"


@pytest.mark.parametrize(
    ("monsters", "areas", "settings", "message"),
    [
        ([DEFAULT_ROW, NAMED_ROW, NAMED_ROW], [], SETTINGS, "duplicate"),
        ([MonsterRowFields("", "9", False, "", "", "follow")], [], SETTINGS, "priority"),
        ([MonsterRowFields("", "x", False, "", "", "follow")], [], SETTINGS, "not a number"),
        ([DEFAULT_ROW], [AreaRuleFields("F7", "0", "")], SETTINGS, "minimum_adjacent_monsters"),
        ([DEFAULT_ROW], [], TargetingSettings("-1", "6"), "cooldown"),
        ([DEFAULT_ROW], [], TargetingSettings("1500", "0"), "unreachable"),
    ],
)
def test_a_refused_value_raises_a_config_error(
    monsters: list[MonsterRowFields],
    areas: list[AreaRuleFields],
    settings: TargetingSettings,
    message: str,
) -> None:
    with pytest.raises(ConfigError, match=message):
        apply_targeting(BotConfig.default(), monsters, areas, settings)


def test_every_movement_has_a_menu_text() -> None:
    assert tuple(MOVEMENT_LABELS) == MOVEMENTS


def test_an_unticked_area_rule_is_saved_off() -> None:
    cfg = apply_targeting(
        BotConfig.default(), [DEFAULT_ROW], [AreaRuleFields("F7", "2", "", enabled=False)], SETTINGS
    )
    assert cfg.targeting is not None
    assert cfg.targeting.area_rules[0].enabled is False


def test_rows_still_being_filled_in_are_not_saved() -> None:
    cfg = apply_targeting(
        BotConfig.default(),
        [DEFAULT_ROW, DEFAULT_ROW._replace(name=" ")],
        [AreaRuleFields("", "2", "", enabled=False)],
        SETTINGS,
    )
    assert cfg.targeting is not None
    assert cfg.targeting.monsters == () and cfg.targeting.area_rules == ()
