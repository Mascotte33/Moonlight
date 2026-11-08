"""T1.F5.K1: attack hotkeys -- area rules first, then the target row's hotkey."""

from __future__ import annotations

from moonlight.core.state import Monster, Position, WorldState
from moonlight.hardware.input import FakeInput
from moonlight.targeting.attack_hotkeys import AttackHotkeys, AttackPress
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
    attack = hotkeys(AreaRule("F5", minimum_monsters=3))
    assert attack.decide(state(AROUND[:3]), ROW, 0.0) == AttackPress("F5")
    assert attack.decide(state(AROUND[:2]), ROW, 0.0) == AttackPress("F6")


def test_every_square_around_counts_and_a_far_slot_does_not() -> None:
    attack = hotkeys(AreaRule("F5", minimum_monsters=8))
    assert attack.decide(state(AROUND), ROW, 0.0) == AttackPress("F5")
    two_away = (*AROUND[:7], (9, 5))
    assert attack.decide(state(two_away), ROW, 0.0) == AttackPress("F6")


def test_first_due_rule_wins_and_area_comes_before_the_row() -> None:
    attack = hotkeys(
        AreaRule("F7", minimum_monsters=4),
        AreaRule("F5", minimum_monsters=2),
        AreaRule("F8", minimum_monsters=1),
    )
    assert attack.decide(state(AROUND[:4]), ROW, 0.0) == AttackPress("F7")
    assert attack.decide(state(AROUND[:2]), ROW, 0.0) == AttackPress("F5")
    assert attack.decide(state(AROUND[:1]), ROW, 0.0) == AttackPress("F8")


def test_mana_minimum_is_in_mana_points() -> None:
    attack = hotkeys(AreaRule("F5", minimum_monsters=1, minimum_mana=200))
    assert attack.decide(state(AROUND[:1], mana=199), ROW, 0.0) == AttackPress("F6")
    assert attack.decide(state(AROUND[:1], mana=200), ROW, 0.0) == AttackPress("F5")


def test_mana_minimum_blocks_a_rule_and_the_row() -> None:
    attack = hotkeys(AreaRule("F5", minimum_monsters=1, minimum_mana=30))
    assert attack.decide(state(AROUND[:1], mana=29), ROW, 0.0) == AttackPress("F6")
    assert attack.decide(state(AROUND[:1], mana=5), ROW, 0.0) is None


def test_unknown_mana_blocks_rules_with_a_minimum_only() -> None:
    attack = hotkeys(AreaRule("F5", minimum_monsters=1, minimum_mana=1))
    assert attack.decide(state(AROUND[:1], mana=0), ROW, 0.0) is None
    melee_free = MonsterRow(hotkey="F6")
    assert attack.decide(state(mana=0), melee_free, 0.0) == AttackPress("F6")


def test_empty_row_hotkey_is_melee_only() -> None:
    assert hotkeys().decide(state(), MonsterRow(), 0.0) is None


def test_cooldown_is_shared_by_area_and_row_keys() -> None:
    attack = hotkeys(AreaRule("F5", minimum_monsters=3))
    inp = FakeInput()
    attack.send("F5", inp, 10.0)
    assert attack.decide(state(), ROW, 11.9) is None  # the row key waits too
    assert attack.decide(state(), ROW, 12.0) == AttackPress("F6")
    assert inp.sent_lines == ["K F5"]


def test_a_turned_off_area_rule_is_skipped() -> None:
    attack = hotkeys(
        AreaRule("F7", minimum_monsters=1, enabled=False),
        AreaRule("F5", minimum_monsters=1),
    )
    assert attack.decide(state(AROUND[:2]), ROW, 0.0) == AttackPress("F5")


# doc: docs/prs/first-spell.md#fresh-cooldown
def test_a_new_target_gets_its_first_spell_at_once() -> None:
    keys = hotkeys()
    keys.send("F6", FakeInput(), 0.0)  # the last spell on the old target
    assert keys.decide(state(), ROW, 0.5) is None  # same target: wait the 2 s
    keys.new_target()  # it died, the next one is selected
    assert keys.decide(state(), ROW, 0.5) == AttackPress("F6")


# doc: docs/prs/attack-types.md#area-kinds
def test_around_me_counts_creatures_on_the_drawn_shape() -> None:
    cross = ((0, -1), (0, 1), (-1, 0), (1, 0))
    attack = hotkeys(AreaRule("F5", minimum_monsters=2, area=cross))
    assert attack.decide(state(((7, 4), (7, 6))), ROW, 0.0) == AttackPress("F5")
    assert attack.decide(state(((7, 4), (8, 4))), ROW, 0.0) == AttackPress("F6")  # a corner


def test_wave_turns_to_the_side_with_most_creatures() -> None:
    beam = ((0, -1), (0, -2), (0, -3))  # drawn facing up
    attack = hotkeys(AreaRule("F5", "wave", minimum_monsters=2, area=beam))
    left = ((6, 5), (5, 5), (7, 4))
    assert attack.decide(state(left), ROW, 0.0) == AttackPress("F5", turn_key="ctrl+left")
    right = ((8, 5), (10, 5))
    assert attack.decide(state(right), ROW, 0.0) == AttackPress("F5", turn_key="ctrl+right")
    assert attack.decide(state(((7, 6),)), ROW, 0.0) == AttackPress("F6")  # 1 below


def test_on_a_tile_aims_where_most_creatures_are_hit() -> None:
    attack = hotkeys(AreaRule("F5", "on a tile", minimum_monsters=3))  # 3x3 square
    pack = ((11, 2), (12, 2), (12, 3), (2, 8))
    assert attack.decide(state(pack), ROW, 0.0) == AttackPress("F5", aim_slot=(11, 3))
    assert attack.decide(state(pack[:2]), ROW, 0.0) == AttackPress("F6")


def test_on_a_tile_never_aims_at_a_wall() -> None:
    ws = state(((11, 2), (12, 2), (12, 3)))
    ws.position = Position(100, 100, 7)
    # World x 104 (slot 11) is a wall: the next best 3-hit centre is slot 12.
    attack = AttackHotkeys(
        TargetingConfig(area_rules=(AreaRule("F5", "on a tile", minimum_monsters=3),)),
        walkable=lambda x, y, z: x != 104,
    )
    assert attack.decide(ws, ROW, 0.0) == AttackPress("F5", aim_slot=(12, 3))


def test_target_slot_picks_the_nearest_matching_bar() -> None:
    ws = state(((2, 2), (9, 6), (12, 5)))
    ws.creature_hp_percents = (50.0, 52.0, 10.0)
    ws.selected_target = Monster("cyclops", hp_percent=50.0)
    assert target_slot(ws) == (9, 6)
    ws.selected_target = None
    assert target_slot(ws) is None
