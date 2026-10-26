"""Unit tests for moonlight.targeting.targeting (monster rows, target choice)."""

from __future__ import annotations

import pytest

from moonlight.core.config import BotConfig, ConfigError
from moonlight.core.state import Monster, WorldState
from moonlight.targeting.targeting import (
    MonsterRow,
    TargetingAction,
    TargetingConfig,
    TargetingDecision,
)


def state(monsters: list[Monster], selected: Monster | None = None) -> WorldState:
    s = WorldState.default()
    s.monsters = monsters
    s.selected_target = selected
    return s


def rows(*monster_rows: MonsterRow, default: MonsterRow | None = None) -> TargetingDecision:
    return TargetingDecision(
        TargetingConfig(monsters=monster_rows, default_monster=default or MonsterRow())
    )


def picked(targeting: TargetingDecision, s: WorldState) -> int | None:
    action = targeting.decide(s, 0.0)
    if action is None:
        return None
    assert action.kind == "select"
    return action.battle_list_row


def test_empty_battle_list_is_idle() -> None:
    assert picked(rows(), state([])) is None


def test_priority_wins_over_hp() -> None:
    monsters = [Monster("Rat", 10.0), Monster("Cyclops", 90.0, battle_list_row=1)]
    assert picked(rows(MonsterRow("cyclops", priority=3)), state(monsters)) == 1


def test_hp_wins_over_row() -> None:
    monsters = [Monster("Rat", 80.0, battle_list_row=0), Monster("Rat", 20.0, battle_list_row=1)]
    assert picked(rows(), state(monsters)) == 1


def test_lowest_row_breaks_a_tie() -> None:
    monsters = [Monster("Rat", 50.0, battle_list_row=2), Monster("Rat", 50.0, battle_list_row=1)]
    assert picked(rows(), state(monsters)) == 1


def test_stays_on_a_valid_target() -> None:
    weak = Monster("Rat", 10.0, battle_list_row=0)
    selected = Monster("Rat", 90.0, battle_list_row=1)
    assert picked(rows(), state([weak, selected], selected)) is None


def test_switches_when_a_higher_priority_appears() -> None:
    selected = Monster("Rat", 10.0, battle_list_row=0)
    boss = Monster("Cyclops", 100.0, battle_list_row=1)
    targeting = rows(MonsterRow("Cyclops", priority=5))
    assert picked(targeting, state([selected], selected)) is None
    assert picked(targeting, state([selected, boss], selected)) == 1


def test_selected_target_that_left_is_replaced() -> None:
    gone = Monster("Rat", 10.0, battle_list_row=3)
    assert picked(rows(), state([Monster("Wolf", 50.0)], gone)) == 0


def test_ignored_row_is_never_chosen() -> None:
    rat = Monster("RAT", 1.0, battle_list_row=0)
    targeting = rows(MonsterRow("rat", ignore=True))
    assert picked(targeting, state([rat])) is None
    assert picked(targeting, state([rat, Monster("Wolf", 99.0, battle_list_row=1)])) == 1
    assert picked(targeting, state([rat], rat)) is None  # never switched to, nothing else


def test_nameless_monster_uses_default_monster() -> None:
    nameless = Monster("", 50.0, battle_list_row=0)
    rat = Monster("Rat", 50.0, battle_list_row=1)
    assert picked(rows(MonsterRow("Rat", priority=2)), state([nameless, rat])) == 1
    assert picked(rows(default=MonsterRow(priority=3)), state([nameless, rat])) == 0
    assert picked(rows(default=MonsterRow(ignore=True)), state([nameless])) is None


def test_players_are_never_chosen() -> None:
    assert picked(rows(), state([Monster("Bob", 10.0, is_player=True)])) is None


def test_row_center_matches_the_reader_geometry() -> None:
    targeting = TargetingDecision(None, (100, 200, 170, 300), ui_scale=1.0)
    assert targeting.row_center(0) == (100 + 23 + 57, 200 + 11 + 11)
    assert targeting.row_center(3) == (180, 200 + 11 + 3 * 22 + 11)
    scaled = TargetingDecision(None, (0, 0, 340, 600), ui_scale=2.0)
    assert scaled.row_center(1) == (46 + 115, 22 + 44 + 22)


def test_row_center_without_a_region_raises() -> None:
    with pytest.raises(ValueError, match="battle_list"):
        TargetingDecision().row_center(0)


@pytest.mark.parametrize(
    "row",
    [
        MonsterRow("rat", priority=0),
        MonsterRow("rat", priority=6),
        MonsterRow("rat", movement="run"),
        MonsterRow("rat", hotkey="F 6"),
        MonsterRow(""),
    ],
)
def test_bad_rows_refused(row: MonsterRow) -> None:
    with pytest.raises(ValueError):
        TargetingConfig(monsters=(row,)).validate()


def test_duplicate_names_refused_ignoring_case() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        TargetingConfig(monsters=(MonsterRow("Rat"), MonsterRow("rat"))).validate()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("mode", "all"),
        ("creatures", ["Rat"]),
        ("prefer_low_hp", True),
        ("max_monsters_to_engage", 4),
        ("player_protection", True),
    ],
)
def test_old_config_keys_raise(key: str, value: object) -> None:
    data = BotConfig.default().to_dict()
    data["modules"]["targeting"][key] = value
    with pytest.raises(ConfigError, match=key):
        BotConfig.from_dict(data)


def test_rows_load_from_json() -> None:
    data = BotConfig.default().to_dict()
    data["modules"]["targeting"]["monsters"] = [
        {"name": "cyclops", "priority": 3, "hotkey": "F6", "minimum_mana_percent": 10},
        {"name": "rat", "ignore": True},
    ]
    cfg = BotConfig.from_dict(data)
    assert cfg.targeting is not None
    assert cfg.targeting.monsters[0] == MonsterRow("cyclops", 3, False, "F6", 10.0, "follow")
    assert cfg.targeting.monsters[1].ignore


# -- unreachable drop ----------------------------------------------------------


def stuck_state(slots: tuple[tuple[int, int], ...] = ((7, 2),), hp: float = 100.0) -> WorldState:
    wolf = Monster("Wolf", hp, name_hash="w", battle_list_row=0)
    rat = Monster("Rat", 100.0, name_hash="r", battle_list_row=1)
    s = state([wolf, rat], wolf)
    s.creature_slots = slots
    return s


def test_stuck_hp_steps_toward_the_nearest_far_creature() -> None:
    targeting = rows()
    s = stuck_state(((7, 4), (11, 5), (7, 2)))  # (7, 4) is adjacent; (7, 2) is nearest
    assert targeting.decide(s, 0.0) is None
    assert targeting.decide(s, 7.9) is None
    assert targeting.decide(s, 8.0) == TargetingAction(
        "step_closer", 0, (0, -1), "Wolf hp stuck at 100, step closer"
    )


def test_still_stuck_after_the_step_ignores_the_target_and_picks_the_next() -> None:
    targeting = rows()
    s = stuck_state()
    targeting.decide(s, 0.0)
    targeting.decide(s, 8.0)
    assert targeting.decide(s, 15.9) is None
    assert picked_at(targeting, s, 16.0) == 1
    assert targeting.is_unreachable(s.monsters[0])


def picked_at(targeting: TargetingDecision, s: WorldState, now: float) -> int | None:
    action = targeting.decide(s, now)
    return None if action is None else action.battle_list_row


def test_an_hp_change_resets_the_watch() -> None:
    targeting = rows()
    targeting.decide(stuck_state(), 0.0)
    targeting.decide(stuck_state(hp=90.0), 7.0)  # HP moved: watch restarts
    assert targeting.decide(stuck_state(hp=90.0), 8.0) is None
    action = targeting.decide(stuck_state(hp=90.0), 15.0)
    assert action is not None and action.kind == "step_closer"


def test_ignored_target_can_be_chosen_again_after_30_seconds() -> None:
    targeting = rows()
    s = stuck_state()
    s.selected_target = None  # the outline is gone: only the rat is chosen meanwhile
    targeting.decide(stuck_state(), 0.0)
    targeting.decide(stuck_state(), 8.0)
    targeting.decide(stuck_state(), 16.0)  # ignored until 46.0
    assert picked_at(targeting, s, 45.9) == 1
    assert picked_at(targeting, s, 46.0) == 0


def test_no_creature_slot_means_no_step() -> None:
    targeting = rows()
    for slots in ((), ((7, 4), (8, 5))):  # none, or only adjacent ones
        s = stuck_state(slots)
        targeting.decide(s, 0.0)
        assert targeting.decide(s, 8.0) is None


def test_chase_hotkeys_must_differ() -> None:
    with pytest.raises(ValueError, match="must differ"):
        TargetingConfig(chase_on_hotkey="shift+F9", chase_off_hotkey="Shift+F9").validate()


def test_chase_hotkey_is_required() -> None:
    with pytest.raises(ValueError):
        TargetingConfig(chase_on_hotkey="").validate()
