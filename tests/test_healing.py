"""H1.F2.K1: healing rules and cures (docs/prs/healing.md#healing-rules).

Hermetic: synthetic WorldState, a fake clock and FakeInput.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from moonlight.core.state import WorldState
from moonlight.hardware.input import FakeInput
from moonlight.healing.healing import (
    CONDITIONS,
    CureRule,
    HealingConfig,
    HealingDecision,
    HealingRule,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def state(hp: float = 100.0, mp: float = 100.0, icons: tuple[str, ...] = ()) -> WorldState:
    s = WorldState.default()
    s.hp_percent, s.mp_percent = hp, mp
    s.active_status_icons = frozenset(icons)
    return s


def keys(healer: HealingDecision, s: WorldState) -> list[str]:
    return [a.hotkey for a in healer.decide(s)]


def cures_on(*conditions: str) -> tuple[CureRule, ...]:
    return tuple(replace(c, enabled=c.condition in conditions) for c in HealingConfig().cures)


def test_defaults() -> None:
    cfg = HealingConfig()
    assert [(r.kind, r.hotkey) for r in cfg.rules] == [
        ("spell", "F2"), ("spell", "F1"), ("potion", "F3"), ("potion", "F4"),
    ]
    assert [(c.condition, c.enabled, c.hotkey) for c in cfg.cures] == [
        (c, False, f"shift+F{i}") for i, c in enumerate(CONDITIONS, start=1)
    ]
    assert (cfg.spell_cooldown_ms, cfg.potion_cooldown_ms) == (1000, 1000)


def test_every_due_rule_is_pressed() -> None:
    healer = HealingDecision()
    assert keys(healer, state(hp=80)) == ["F1"]
    assert keys(healer, state(hp=45)) == ["F2", "F1"]  # both due: both, in list order
    assert keys(healer, state(hp=100)) == []


def test_hp_and_mp_triggers() -> None:
    rules = (
        HealingRule("spell", "F5", hp_below_percent=60),
        HealingRule("potion", "F4", mp_below_percent=30),
    )
    healer = HealingDecision(HealingConfig(rules=rules, cures=()))
    assert keys(healer, state(hp=60, mp=50)) == ["F5"]  # at the trigger counts
    assert keys(healer, state(hp=60, mp=5)) == ["F5", "F4"]  # low mana still heals
    assert keys(healer, state(hp=90, mp=30)) == ["F4"]


def test_unknown_readings() -> None:
    rules = (HealingRule("spell", "F5", hp_below_percent=60),)
    healer = HealingDecision(HealingConfig(rules=rules, cures=()))
    assert keys(healer, state(hp=0, mp=50)) == []  # unknown HP never heals
    assert keys(healer, state(hp=50, mp=0)) == ["F5"]
    mp_rule = (HealingRule("potion", "F4", mp_below_percent=30),)
    assert keys(HealingDecision(HealingConfig(rules=mp_rule, cures=())), state(mp=0)) == []


def test_spell_and_potion_in_one_tick() -> None:
    healer = HealingDecision(clock=FakeClock())
    actions = healer.decide(state(hp=30, mp=20))
    assert [(a.slot, a.hotkey) for a in actions] == [
        ("spell", "F2"), ("spell", "F1"), ("potion", "F3"), ("potion", "F4"),
    ]  # fmt: skip
    backend = FakeInput()
    for action in actions:
        healer.send(action, backend)
    assert backend.sent_lines == ["K F2", "K F1", "K F3", "K F4"]


# doc: docs/prs/human-timing.md#heal-reaction
def test_heal_waits_a_reaction_unless_hp_is_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("moonlight.core.random_delay.HEAL_REACTION_SECONDS", (0.1, 0.1))
    clock = FakeClock()
    healer = HealingDecision(clock=clock)
    assert keys(healer, state(hp=80)) == []  # F1 just became due
    clock.now = 0.1
    assert keys(healer, state(hp=80)) == ["F1"]
    assert keys(HealingDecision(clock=clock), state(hp=40)) == ["F2", "F1", "F3"]  # 40%: at once


def test_each_rule_has_its_own_cooldown() -> None:
    clock = FakeClock()
    healer = HealingDecision(HealingConfig(potion_cooldown_ms=500), clock=clock)
    clock.now = 0.0
    for action in healer.decide(state(hp=30, mp=20)):
        healer.send(action, FakeInput())  # F2, F1, F3, F4
    clock.now = 0.4
    assert keys(healer, state(hp=30, mp=20)) == []  # all cooling
    clock.now = 0.5
    assert keys(healer, state(hp=30, mp=20)) == ["F3", "F4"]  # the potions cooled down


def test_out_of_health_potions_the_mana_potion_brings_the_heal_spell_back() -> None:
    # 2026-11-27: out of health potions, the bot pressed the empty F3 every second
    # and never the mana potion on F4; with 27 mana the heal spell (60) never went,
    # and the character died. The Yalahar Elves rules, replayed tick by tick.
    clock = FakeClock()
    rules = (
        HealingRule("spell", "F1", hp_below_percent=35.0, minimum_mana=60),
        HealingRule("potion", "F3", hp_below_percent=50.0),
        HealingRule("potion", "F4", mp_below_percent=40.0),
    )
    healer = HealingDecision(HealingConfig(rules=rules), clock=clock)
    backend = FakeInput()

    def tick(now: float, mp: float, mana: int) -> list[str]:
        clock.now = now
        s = state(hp=14, mp=mp)
        s.mp_points = mana
        before = len(backend.sent_lines)
        for action in healer.decide(s):
            healer.send(action, backend)
        return backend.sent_lines[before:]

    assert tick(0.0, mp=14, mana=27) == ["K F3", "K F4"]  # the empty F3 blocks nothing
    assert tick(0.5, mp=14, mana=27) == []  # both cooling down
    assert tick(1.0, mp=14, mana=27) == ["K F3", "K F4"]  # still no mana for the spell
    # The mana potions bring mana back over the spell's 60:
    assert tick(2.0, mp=41, mana=80) == ["K F1", "K F3"]  # the heal spell goes


def test_cures_go_with_the_heals() -> None:
    healer = HealingDecision(HealingConfig(cures=cures_on("poison", "paralyse")))
    poisoned = ("paralyse", "poison")
    assert keys(healer, state(icons=poisoned)) == ["shift+F1", "shift+F5"]  # poison first
    assert keys(healer, state(hp=80, icons=("poison",))) == ["F1", "shift+F1"]  # heal first
    assert keys(healer, state(hp=95, mp=20, icons=("poison",))) == ["shift+F1", "F4"]
    assert keys(healer, state(icons=("burn",))) == []  # burn cure is off


def test_cure_goes_while_the_spell_rule_cools_down() -> None:
    clock = FakeClock()
    healer = HealingDecision(HealingConfig(cures=cures_on("poison")), clock=clock)
    (heal,) = healer.decide(state(hp=80))
    healer.send(heal, FakeInput())
    assert keys(healer, state(hp=80, icons=("poison",))) == ["shift+F1"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"rules": (HealingRule("spell", "F1"),)}, "needs hp_below_percent"),
        ({"rules": (HealingRule("spell", "F1", hp_below_percent=101),)}, "0..100"),
        ({"rules": (HealingRule("spell", "F1", mp_below_percent=-1),)}, "0..100"),
        ({"rules": (HealingRule("rune", "F1", hp_below_percent=50),)}, "kind"),
        ({"rules": (HealingRule("spell", "", hp_below_percent=50),)}, "hotkey"),
        ({"rules": (HealingRule("spell", "F 1", hp_below_percent=50),)}, "hotkey"),
        ({"cures": (CureRule("curse", True, "F6"),)}, "unknown condition"),
        ({"cures": (CureRule("burn", True, "F6"), CureRule("burn", False, "F7"))}, "twice"),
        ({"cures": (CureRule("burn", False, ""),)}, "hotkey"),
        ({"spell_cooldown_ms": 0}, "cooldown"),
        ({"potion_cooldown_ms": -5}, "cooldown"),
    ],
)
def test_validation_errors(change: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        HealingDecision(replace(HealingConfig(), **change))  # type: ignore[arg-type]


def test_a_spell_rule_without_its_mana_is_skipped_for_the_next() -> None:
    from moonlight.healing.healing import HealingConfig, HealingDecision, HealingRule

    config = HealingConfig(
        rules=(
            HealingRule("spell", "F1", hp_below_percent=85.0, minimum_mana=40),
            HealingRule("spell", "F2", hp_below_percent=85.0),
        ),
    )
    decision = HealingDecision(config, clock=lambda: 100.0)
    low = WorldState(hp_percent=50.0, mp_percent=10.0, mp_points=15)
    actions = decision.decide(low)
    assert [a.hotkey for a in actions] == ["F2"]  # F1 needs 40 mana: the next rule heals
