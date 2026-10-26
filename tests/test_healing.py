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


def test_first_due_rule_of_a_kind_wins() -> None:
    healer = HealingDecision()
    assert keys(healer, state(hp=80)) == ["F1"]
    assert keys(healer, state(hp=45)) == ["F2"]  # F2 is listed first
    assert keys(healer, state(hp=100)) == []


def test_hp_and_mp_triggers_and_mana_floor() -> None:
    rules = (
        HealingRule("spell", "F5", hp_below_percent=60, minimum_mana_percent=40),
        HealingRule("potion", "F4", mp_below_percent=30),
    )
    healer = HealingDecision(HealingConfig(rules=rules, cures=()))
    assert keys(healer, state(hp=60, mp=50)) == ["F5"]  # at the trigger counts
    assert keys(healer, state(hp=60, mp=35)) == []  # below the mana floor
    assert keys(healer, state(hp=90, mp=30)) == ["F4"]


def test_unknown_readings() -> None:
    rules = (HealingRule("spell", "F5", hp_below_percent=60, minimum_mana_percent=40),)
    healer = HealingDecision(HealingConfig(rules=rules, cures=()))
    assert keys(healer, state(hp=0, mp=50)) == []  # unknown HP never heals
    assert keys(healer, state(hp=50, mp=0)) == ["F5"]  # unknown MP passes the floor
    mp_rule = (HealingRule("potion", "F4", mp_below_percent=30),)
    assert keys(HealingDecision(HealingConfig(rules=mp_rule, cures=())), state(mp=0)) == []


def test_spell_and_potion_in_one_tick() -> None:
    healer = HealingDecision(clock=FakeClock())
    actions = healer.decide(state(hp=30, mp=20))
    assert [(a.slot, a.hotkey) for a in actions] == [("spell", "F2"), ("potion", "F3")]
    backend = FakeInput()
    for action in actions:
        healer.send(action, backend)
    assert backend.sent_lines == ["K F2", "K F3"]


def test_each_slot_has_its_own_cooldown() -> None:
    clock = FakeClock()
    healer = HealingDecision(HealingConfig(potion_cooldown_ms=500), clock=clock)
    for action in healer.decide(state(hp=30)):
        healer.send(action, FakeInput())
    clock.now = 0.4
    assert keys(healer, state(hp=30)) == []  # no weaker rule takes a cooling slot
    clock.now = 0.5
    assert keys(healer, state(hp=30)) == ["F3"]
    clock.now = 1.0
    assert keys(healer, state(hp=30)) == ["F2", "F3"]


def test_cure_only_when_no_spell_rule_is_due() -> None:
    healer = HealingDecision(HealingConfig(cures=cures_on("poison", "paralyse")))
    poisoned = ("paralyse", "poison")
    assert keys(healer, state(icons=poisoned)) == ["shift+F1"]  # poison before paralyse
    assert keys(healer, state(hp=80, icons=poisoned)) == ["F1"]  # a needed heal wins
    assert keys(healer, state(hp=95, mp=20, icons=poisoned)) == ["shift+F1", "F4"]
    assert keys(healer, state(icons=("burn",))) == []  # burn cure is off


def test_cure_waits_for_a_spell_rule_on_cooldown() -> None:
    clock = FakeClock()
    healer = HealingDecision(HealingConfig(cures=cures_on("poison")), clock=clock)
    (heal,) = healer.decide(state(hp=80))
    healer.send(heal, FakeInput())
    assert keys(healer, state(hp=80, icons=("poison",))) == []


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
