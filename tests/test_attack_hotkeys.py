"""T1.F5.K1: attack hotkeys -- area rules first, then the target row's hotkey."""

from __future__ import annotations

from moonlight.core.state import Monster, WorldState
from moonlight.hardware.input import FakeInput
from moonlight.targeting.attack_hotkeys import AttackHotkeys
from moonlight.targeting.targeting import AreaRule, MonsterRow, TargetingConfig, target_slot

#: The 8 slots around the player's (7, 5).
AROUND = ((6, 4), (7, 4), (8, 4), (6, 5), (8, 5), (6, 6), (7, 6), (8, 6))
ROW = MonsterRow(name="cyclops", hotkey="F6", minimum_mana=10)


def state(slots: tuple[tuple[int, int], ...] = (), mana: int = 80) -> WorldState:
    ws = WorldState()
    ws.creature_slots = slots
    ws.mp_points = mana
    return ws


def hotkeys(*rules: AreaRule) -> AttackHotkeys:
    return AttackHotkeys(TargetingConfig(area_rules=rules))


def test_area_rule_fires_at_n_adjacent_not_at_n_minus_1() -> None:
    attack = hotkeys(AreaRule("F5", minimum_adjacent_monsters=3))
    assert attack.decide(state(AROUND[:3]), ROW, 0.0) == ("F5", None)
    assert attack.decide(state(AROUND[:2]), ROW, 0.0) == ("F6", None)


def test_every_square_around_counts_and_a_far_slot_does_not() -> None:
    attack = hotkeys(AreaRule("F5", minimum_adjacent_monsters=8))
    assert attack.decide(state(AROUND), ROW, 0.0) == ("F5", None)
    two_away = (*AROUND[:7], (9, 5))
    assert attack.decide(state(two_away), ROW, 0.0) == ("F6", None)


def test_first_due_rule_wins_and_area_comes_before_the_row() -> None:
    attack = hotkeys(
        AreaRule("F7", minimum_adjacent_monsters=4),
        AreaRule("F5", minimum_adjacent_monsters=2),
        AreaRule("F8", minimum_adjacent_monsters=1),
    )
    assert attack.decide(state(AROUND[:4]), ROW, 0.0) == ("F7", None)
    assert attack.decide(state(AROUND[:2]), ROW, 0.0) == ("F5", None)
    assert attack.decide(state(AROUND[:1]), ROW, 0.0) == ("F8", None)


def test_mana_minimum_is_in_mana_points() -> None:
    attack = hotkeys(AreaRule("F5", minimum_adjacent_monsters=1, minimum_mana=200))
    assert attack.decide(state(AROUND[:1], mana=199), ROW, 0.0) == ("F6", None)
    assert attack.decide(state(AROUND[:1], mana=200), ROW, 0.0) == ("F5", None)


def test_mana_minimum_blocks_a_rule_and_the_row() -> None:
    attack = hotkeys(AreaRule("F5", minimum_adjacent_monsters=1, minimum_mana=30))
    assert attack.decide(state(AROUND[:1], mana=29), ROW, 0.0) == ("F6", None)
    assert attack.decide(state(AROUND[:1], mana=5), ROW, 0.0) is None


def test_unknown_mana_blocks_rules_with_a_minimum_only() -> None:
    attack = hotkeys(AreaRule("F5", minimum_adjacent_monsters=1, minimum_mana=1))
    assert attack.decide(state(AROUND[:1], mana=0), ROW, 0.0) is None
    melee_free = MonsterRow(hotkey="F6")
    assert attack.decide(state(mana=0), melee_free, 0.0) == ("F6", None)


def test_empty_row_hotkey_is_melee_only() -> None:
    assert hotkeys().decide(state(), MonsterRow(), 0.0) is None


def test_cooldown_is_shared_by_area_and_row_keys() -> None:
    attack = hotkeys(AreaRule("F5", minimum_adjacent_monsters=3))
    inp = FakeInput()
    attack.send("F5", inp, 10.0)
    assert attack.decide(state(), ROW, 11.9) is None  # the row key waits too
    assert attack.decide(state(), ROW, 12.0) == ("F6", None)
    assert inp.sent_lines == ["K F5"]


def test_a_turned_off_area_rule_is_skipped() -> None:
    attack = hotkeys(
        AreaRule("F7", minimum_adjacent_monsters=1, enabled=False),
        AreaRule("F5", minimum_adjacent_monsters=1),
    )
    assert attack.decide(state(AROUND[:2]), ROW, 0.0) == ("F5", None)


# doc: docs/prs/first-spell.md#fresh-cooldown
def test_a_new_target_gets_its_first_spell_at_once() -> None:
    keys = hotkeys()
    keys.send("F6", FakeInput(), 0.0)  # the last spell on the old target
    assert keys.decide(state(), ROW, 0.5) is None  # same target: wait the 2 s
    keys.new_target()  # it died, the next one is selected
    assert keys.decide(state(), ROW, 0.5) == ("F6", None)


def target_state(slot: tuple[int, int], target_hp: float = 40.0) -> WorldState:
    """A creature at 40 % HP on *slot* and one at full HP; the target shows *target_hp*."""
    ws = state((slot, (1, 1)))
    ws.creature_hp_percents = (40.0, 100.0)
    ws.selected_target = Monster("cyclops", hp_percent=target_hp)
    return ws


# doc: docs/prs/attack-types.md#attack-type
def test_melee_key_fires_only_next_to_the_target() -> None:
    melee = MonsterRow(name="cyclops", hotkey="F6", attack_type="melee")
    assert hotkeys().decide(target_state((8, 6)), melee, 0.0) == ("F6", None)
    assert hotkeys().decide(target_state((9, 5)), melee, 0.0) is None
    assert hotkeys().decide(target_state((9, 5), target_hp=70), melee, 0.0) is None  # no bar


def test_rune_key_aims_at_the_target_slot() -> None:
    rune = MonsterRow(name="cyclops", hotkey="F6", attack_type="rune")
    assert hotkeys().decide(target_state((10, 3)), rune, 0.0) == ("F6", (10, 3))
    assert hotkeys().decide(target_state((10, 3), target_hp=70), rune, 0.0) is None


def test_target_slot_picks_the_nearest_matching_bar() -> None:
    ws = state(((2, 2), (9, 6), (12, 5)))
    ws.creature_hp_percents = (50.0, 52.0, 10.0)
    ws.selected_target = Monster("cyclops", hp_percent=50.0)
    assert target_slot(ws) == (9, 6)
    ws.selected_target = None
    assert target_slot(ws) is None
