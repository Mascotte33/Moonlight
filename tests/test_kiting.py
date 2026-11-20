"""Kiting: keep distance from every monster while in danger, never stop attacking."""

# doc: docs/prs/kiting.md#kiting

from __future__ import annotations

from dataclasses import replace

import pytest

from moonlight.core.state import Monster, WorldState
from moonlight.targeting.targeting import MonsterRow, TargetingConfig, TargetingDecision

#: Three creatures right next to the player (slot 7,5), all on its right.
NEXT_TO_ME = ((8, 4), (8, 5), (8, 6))


def kite_state(hp: float, slots: tuple[tuple[int, int], ...] = NEXT_TO_ME) -> WorldState:
    wolf = Monster("Wolf", hp_percent=60.0, battle_list_row=0)
    s = WorldState.default()
    s.monsters = [wolf, Monster("Wolf", 100.0, battle_list_row=1)]
    s.selected_target = wolf
    s.creature_slots = slots
    s.creature_hp_percents = (60.0,) + (100.0,) * (len(slots) - 1)
    s.hp_percent = hp
    return s


def kiting(on: bool = True) -> TargetingDecision:
    return TargetingDecision(
        TargetingConfig(monsters=(MonsterRow(name="Wolf"),), kite_when_in_danger=on)
    )


def test_off_by_default_follow_rows_never_step_away() -> None:
    targeting = kiting(on=False)
    assert targeting.decide(kite_state(hp=10.0), 0.0) is None
    assert not targeting.kiting


def test_low_hp_with_three_monsters_next_to_me_steps_away() -> None:
    targeting = kiting()
    action = targeting.decide(kite_state(hp=15.0), 0.0)
    assert targeting.kiting
    assert action is not None and action.kind == "step_away" and action.step[0] == -1
    assert action.battle_list_row == 0  # still on the same target
    assert action.reason.startswith("kiting from Wolf")


def test_two_monsters_next_to_me_is_no_danger() -> None:
    targeting = kiting()
    assert targeting.decide(kite_state(hp=15.0, slots=NEXT_TO_ME[:2]), 0.0) is None
    assert not targeting.kiting


def test_monsters_two_tiles_away_do_not_count() -> None:
    targeting = kiting()
    targeting.decide(kite_state(hp=15.0, slots=((9, 4), (9, 5), (9, 6))), 0.0)
    assert not targeting.kiting


def test_fast_hp_drop_starts_kiting_above_the_hp_limit() -> None:
    targeting = kiting()
    targeting.decide(kite_state(hp=90.0), 0.0)
    assert not targeting.kiting
    targeting.decide(kite_state(hp=75.0), 0.5)  # 15 points in 0.5 s (alarm: 10 in 1 s)
    assert targeting.kiting


def test_stops_when_hp_is_back_and_no_fast_drop_for_3_seconds() -> None:
    targeting = kiting()
    targeting.decide(kite_state(hp=90.0), 0.0)
    targeting.decide(kite_state(hp=75.0), 0.5)
    assert targeting.kiting
    targeting.decide(kite_state(hp=80.0), 3.4)
    assert targeting.kiting  # 2.9 s since the drop
    targeting.decide(kite_state(hp=80.0), 3.5)
    assert not targeting.kiting


def test_stays_kiting_while_hp_is_below_the_limit() -> None:
    targeting = kiting()
    targeting.decide(kite_state(hp=15.0), 0.0)
    targeting.decide(kite_state(hp=19.0), 10.0)
    assert targeting.kiting


def test_boxed_in_stays_and_keeps_attacking() -> None:
    around = tuple((7 + dx, 5 + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy)
    targeting = kiting()
    assert targeting.decide(kite_state(hp=15.0, slots=around), 0.0) is None  # nothing to change
    assert targeting.kiting


def test_never_gives_up_the_target_while_kiting() -> None:
    """A melee target out of reach keeps its HP: no step closer, no ignore, no pause."""
    targeting = kiting()
    for tick in range(400):  # 20 s at 20 Hz, target HP standing still
        action = targeting.decide(kite_state(hp=15.0), tick * 0.05)
        assert action is None or action.kind == "step_away"
    assert not targeting.is_unreachable(kite_state(hp=15.0).monsters[0])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kite_when_monsters_within_one_tile", 0),
        ("kite_when_monsters_within_one_tile", 9),
        ("kite_when_hp_below_percent", 0),
        ("kite_when_hp_below_percent", 100),
    ],
)
def test_bad_kite_settings_are_refused(field: str, value: int) -> None:
    with pytest.raises(ValueError, match=field):
        replace(TargetingConfig(), **{field: value}).validate()  # type: ignore[arg-type]
