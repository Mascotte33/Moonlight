"""T1.F5.K1: attack hotkeys -- area rules first, then the target row's hotkey."""

from __future__ import annotations

from moonlight.core.state import WorldState
from moonlight.hardware.input import FakeInput
from moonlight.targeting.attack_hotkeys import AttackHotkeys
from moonlight.targeting.targeting import AreaRule, MonsterRow, TargetingConfig

#: The 8 slots around the player's (7, 5).
AROUND = ((6, 4), (7, 4), (8, 4), (6, 5), (8, 5), (6, 6), (7, 6), (8, 6))
ROW = MonsterRow(name="cyclops", hotkey="F6", minimum_mana_percent=10.0)


def state(slots: tuple[tuple[int, int], ...] = (), mana: float = 80.0) -> WorldState:
    ws = WorldState()
    ws.creature_slots = slots
    ws.mp_percent = mana
    return ws


def hotkeys(*rules: AreaRule) -> AttackHotkeys:
    return AttackHotkeys(TargetingConfig(area_rules=rules))


def test_area_rule_fires_at_n_adjacent_not_at_n_minus_1() -> None:
    attack = hotkeys(AreaRule("F5", minimum_adjacent_monsters=3))
    assert attack.decide(state(AROUND[:3]), ROW, 0.0) == "F5"
    assert attack.decide(state(AROUND[:2]), ROW, 0.0) == "F6"


def test_every_square_around_counts_and_a_far_slot_does_not() -> None:
    attack = hotkeys(AreaRule("F5", minimum_adjacent_monsters=8))
    assert attack.decide(state(AROUND), ROW, 0.0) == "F5"
    two_away = (*AROUND[:7], (9, 5))
    assert attack.decide(state(two_away), ROW, 0.0) == "F6"


def test_first_due_rule_wins_and_area_comes_before_the_row() -> None:
    attack = hotkeys(
        AreaRule("F7", minimum_adjacent_monsters=4),
        AreaRule("F5", minimum_adjacent_monsters=2),
        AreaRule("F8", minimum_adjacent_monsters=1),
    )
    assert attack.decide(state(AROUND[:4]), ROW, 0.0) == "F7"
    assert attack.decide(state(AROUND[:2]), ROW, 0.0) == "F5"
    assert attack.decide(state(AROUND[:1]), ROW, 0.0) == "F8"


def test_mana_minimum_blocks_a_rule_and_the_row() -> None:
    attack = hotkeys(AreaRule("F5", minimum_adjacent_monsters=1, minimum_mana_percent=30.0))
    assert attack.decide(state(AROUND[:1], mana=29.0), ROW, 0.0) == "F6"
    assert attack.decide(state(AROUND[:1], mana=5.0), ROW, 0.0) is None


def test_unknown_mana_blocks_rules_with_a_minimum_only() -> None:
    attack = hotkeys(AreaRule("F5", minimum_adjacent_monsters=1, minimum_mana_percent=1.0))
    assert attack.decide(state(AROUND[:1], mana=0.0), ROW, 0.0) is None
    melee_free = MonsterRow(hotkey="F6")
    assert attack.decide(state(mana=0.0), melee_free, 0.0) == "F6"


def test_empty_row_hotkey_is_melee_only() -> None:
    assert hotkeys().decide(state(), MonsterRow(), 0.0) is None


def test_cooldown_is_shared_by_area_and_row_keys() -> None:
    attack = hotkeys(AreaRule("F5", minimum_adjacent_monsters=3))
    inp = FakeInput()
    attack.send("F5", inp, 10.0)
    assert attack.decide(state(), ROW, 11.9) is None  # the row key waits too
    assert attack.decide(state(), ROW, 12.0) == "F6"
    assert inp.sent_lines == ["K F5"]


def test_a_turned_off_area_rule_is_skipped() -> None:
    attack = hotkeys(
        AreaRule("F7", minimum_adjacent_monsters=1, enabled=False),
        AreaRule("F5", minimum_adjacent_monsters=1),
    )
    assert attack.decide(state(AROUND[:2]), ROW, 0.0) == "F5"
