"""Unit tests for moonlight.decision.healing (HealingDecision, T14).

Fully hermetic: synthetic WorldState snapshots, an injectable fake clock,
and FakeInput for the input-backend integration. No hardware, no client.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from moonlight.bot.state import WorldState
from moonlight.decision.healing import (
    EMERGENCY_COOLDOWN_KEY,
    NORMAL_COOLDOWN_KEY,
    HealingAction,
    HealingConfig,
    HealingDecision,
    HealType,
    cure_cooldown_key,
)
from moonlight.hardware.input import FakeInput


class FakeClock:
    """Deterministic monotonic clock (same pattern as test_loop.py)."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


def make_state(
    *,
    hp: float = 100.0,
    mp: float = 100.0,
    buffs: dict[str, float] | None = None,
) -> WorldState:
    """One consistent world-state frame (reader-style, like the loop gets)."""
    state = WorldState.default()
    state.client_focused = True
    state.screen_ok = True
    state.hp_percent = hp
    state.mp_percent = mp
    state.last_update = 1.0
    if buffs is not None:
        state.buffs = dict(buffs)
    return state


#: Cures are off by default; the old shared spell keeps these tests' expectations.
CURED = HealingConfig(
    cure_paralyze="exura gran",
    cure_poison="exura gran",
    cure_burn="exura gran",
    cure_energy="exura gran",
    cure_curse="exura gran",
    cure_bleed="exura gran",
)


def make_healer(
    config: HealingConfig | None = CURED,
    clock: FakeClock | None = None,
    spell_combos: dict[str, str] | None = None,
) -> tuple[HealingDecision, FakeClock]:
    clk = clock if clock is not None else FakeClock()
    healer = HealingDecision(
        config,
        clock=clk,
        spell_combos=spell_combos if spell_combos is not None else {},
    )
    return healer, clk


# -- decide(): thresholds -----------------------------------------------------


class TestDecideThresholds:
    def test_healthy_hp_no_action(self) -> None:
        healer, _ = make_healer()
        assert healer.decide(make_state(hp=100.0)) is None

    def test_unknown_hp_no_action(self) -> None:
        # hp 0.0 means "never read" -> must not heal.
        healer, _ = make_healer()
        assert healer.decide(make_state(hp=0.0)) is None

    def test_emergency_below_threshold(self) -> None:
        healer, _ = make_healer()
        action = healer.decide(make_state(hp=25.0))
        assert action is not None
        assert action.type is HealType.EMERGENCY
        assert action.type.value == "emergency"
        assert action.spell == "exura gran"
        assert action.cooldown_key == EMERGENCY_COOLDOWN_KEY
        assert "hp" in action.reason

    def test_emergency_at_threshold(self) -> None:
        healer, _ = make_healer()
        action = healer.decide(make_state(hp=30.0))
        assert action is not None
        assert action.type is HealType.EMERGENCY

    def test_normal_between_thresholds(self) -> None:
        healer, _ = make_healer()
        action = healer.decide(make_state(hp=50.0))
        assert action is not None
        assert action.type is HealType.NORMAL
        assert action.spell == "exura"
        assert action.cooldown_key == NORMAL_COOLDOWN_KEY

    def test_normal_at_threshold(self) -> None:
        healer, _ = make_healer()
        action = healer.decide(make_state(hp=70.0))
        assert action is not None
        assert action.type is HealType.NORMAL

    def test_normal_above_threshold(self) -> None:
        healer, _ = make_healer()
        assert healer.decide(make_state(hp=71.0)) is None

    def test_custom_thresholds_respected(self) -> None:
        config = HealingConfig(emergency_hp_percent=15.0, normal_hp_percent=50.0)
        healer, _ = make_healer(config=config)
        action = healer.decide(make_state(hp=15.0))
        assert action is not None and action.type is HealType.EMERGENCY
        action = healer.decide(make_state(hp=30.0))
        assert action is not None and action.type is HealType.NORMAL
        assert healer.decide(make_state(hp=51.0)) is None


# -- decide(): priority ---------------------------------------------------------


class TestDecidePriority:
    def test_emergency_beats_cure(self) -> None:
        healer, _ = make_healer()
        action = healer.decide(make_state(hp=10.0, buffs={"paralyze": 5.0}))
        assert action is not None
        assert action.type is HealType.EMERGENCY

    def test_cure_beats_normal(self) -> None:
        healer, _ = make_healer()
        action = healer.decide(make_state(hp=50.0, buffs={"poison": 12.0}))
        assert action is not None
        assert action.type is HealType.CURE
        assert action.spell == "exura gran"
        assert action.cooldown_key == cure_cooldown_key("poison")
        assert "poison" in action.reason
        assert "12.0s" in action.reason

    def test_cure_priority_follows_config_order(self) -> None:
        config = HealingConfig(cure_poison="exana pox", cure_burn="exana flam")
        healer, _ = make_healer(config=config)
        action = healer.decide(
            make_state(hp=50.0, buffs={"burn": 6.0, "poison": 4.0})
        )
        assert action is not None
        # CURES order: poison before burn, each with its own spell.
        assert action.cooldown_key == cure_cooldown_key("poison")
        assert action.spell == "exana pox"

    def test_condition_without_spell_is_not_cured(self) -> None:
        healer, _ = make_healer(config=HealingConfig(cure_poison="exana pox"))
        assert healer.decide(make_state(hp=90.0, buffs={"burn": 6.0})) is None

    def test_case_insensitive_condition_match(self) -> None:
        healer, _ = make_healer()
        action = healer.decide(make_state(hp=50.0, buffs={"Paralyze": 3.0}))
        assert action is not None
        assert action.cooldown_key == cure_cooldown_key("paralyze")

    def test_uncured_condition_ignored(self) -> None:
        healer, _ = make_healer()
        # haste is a buff, not a configured condition.
        assert healer.decide(make_state(hp=90.0, buffs={"haste": 30.0})) is None

    def test_empty_buffs_no_cure(self) -> None:
        healer, _ = make_healer()
        assert healer.decide(make_state(hp=50.0, buffs={})) is not None
        # (normal heal for low HP)
        assert healer.decide(make_state(hp=90.0, buffs={})) is None


# -- decide(): mana gate --------------------------------------------------------


class TestManaGate:
    def test_low_known_mp_blocks_heal(self) -> None:
        healer, _ = make_healer()
        assert healer.decide(make_state(hp=10.0, mp=19.9)) is None

    def test_mp_at_threshold_allowed(self) -> None:
        healer, _ = make_healer()
        action = healer.decide(make_state(hp=10.0, mp=20.0))
        assert action is not None
        assert action.type is HealType.EMERGENCY

    def test_unknown_mp_allows_heal(self) -> None:
        # mp 0.0 = never read; we must still heal at critical HP.
        healer, _ = make_healer()
        action = healer.decide(make_state(hp=10.0, mp=0.0))
        assert action is not None
        assert action.type is HealType.EMERGENCY

    def test_low_mp_blocks_cure_too(self) -> None:
        healer, _ = make_healer()
        assert healer.decide(make_state(hp=90.0, mp=5.0, buffs={"poison": 8.0})) is None

    def test_custom_mana_threshold(self) -> None:
        config = HealingConfig(mana_threshold=40.0)
        healer, _ = make_healer(config=config)
        assert healer.decide(make_state(hp=10.0, mp=35.0)) is None
        assert healer.decide(make_state(hp=10.0, mp=45.0)) is not None


# -- cooldowns --------------------------------------------------------------------


class TestCooldowns:
    def test_no_cooldown_initially(self) -> None:
        healer, _ = make_healer()
        assert not healer.on_cooldown(EMERGENCY_COOLDOWN_KEY)
        assert healer.cooldown_remaining_ms(NORMAL_COOLDOWN_KEY) == 0.0

    def test_cooldown_blocks_immediate_repeat(self) -> None:
        combos = {"exura gran": "f1", "exura": "f2"}
        healer, clk = make_healer(spell_combos=combos)
        backend = FakeInput()
        # First tick: emergency heal sent.
        assert healer.decide_and_send(make_state(hp=10.0), backend) is not None
        # Same instant, HP still critical: on cooldown -> no second cast.
        assert healer.decide(make_state(hp=10.0)) is None
        assert len(backend.sent_lines) == 1

    def test_cooldown_expires(self) -> None:
        combos = {"exura gran": "f1"}
        clk = FakeClock()
        healer = HealingDecision(
            HealingConfig(heal_cooldown_ms=500),
            clock=clk,
            spell_combos=combos,
        )
        backend = FakeInput()
        healer.decide_and_send(make_state(hp=10.0), backend)
        clk.advance(0.4)
        assert healer.on_cooldown(EMERGENCY_COOLDOWN_KEY)
        assert healer.decide(make_state(hp=10.0)) is None
        clk.advance(0.11)
        assert not healer.on_cooldown(EMERGENCY_COOLDOWN_KEY)
        assert healer.decide(make_state(hp=10.0)) is not None
        assert len(backend.sent_lines) == 1

    def test_cooldown_keys_are_independent(self) -> None:
        combos = {"exura gran": "f1"}
        healer, clk = make_healer(spell_combos=combos)
        backend = FakeInput()
        # Emergency heal sent; now paralyze appears -> cure still allowed
        # (different cooldown key).
        healer.decide_and_send(make_state(hp=10.0), backend)
        action = healer.decide(make_state(hp=10.0, buffs={"paralyze": 5.0}))
        assert action is not None
        assert action.cooldown_key == cure_cooldown_key("paralyze")

    def test_send_records_cooldown(self) -> None:
        combos = {"exura": "f2"}
        clk = FakeClock()
        healer = HealingDecision(HealingConfig(), clock=clk, spell_combos=combos)
        backend = FakeInput()
        action = healer.decide(make_state(hp=50.0))
        assert action is not None
        assert healer.send(action, backend) is True
        assert healer.on_cooldown(NORMAL_COOLDOWN_KEY)
        remaining = healer.cooldown_remaining_ms(NORMAL_COOLDOWN_KEY)
        assert 0.0 < remaining <= 500.0
        clk.advance(remaining / 1000.0 + 0.001)
        assert not healer.on_cooldown(NORMAL_COOLDOWN_KEY)

    def test_reset_cooldowns(self) -> None:
        healer, clk = make_healer()
        clk.advance(0.1)
        healer.reset_cooldowns()
        healer._record_sent(EMERGENCY_COOLDOWN_KEY)  # direct bookkeeping for test
        assert healer.on_cooldown(EMERGENCY_COOLDOWN_KEY)
        healer.reset_cooldowns()
        assert not healer.on_cooldown(EMERGENCY_COOLDOWN_KEY)


# -- send(): input integration -----------------------------------------------------


class TestSend:
    def test_send_with_combo_mapping(self) -> None:
        combos = {"exura gran": "f1", "exura": "f2"}
        healer, _ = make_healer(spell_combos=combos)
        backend = FakeInput()
        action = healer.decide(make_state(hp=10.0))
        assert action is not None
        assert healer.send(action, backend) is True
        assert backend.sent_lines == ["K f1"]
        assert backend.last_reply == "OK"

    def test_send_without_combo_mapping_declines(self) -> None:
        healer, _ = make_healer(spell_combos={})
        backend = FakeInput()
        action = healer.decide(make_state(hp=10.0))
        assert action is not None
        assert healer.send(action, backend) is False
        assert backend.sent_lines == []

    def test_send_refuses_while_on_cooldown(self) -> None:
        combos = {"exura gran": "f1"}
        healer, _ = make_healer(spell_combos=combos)
        backend = FakeInput()
        action = healer.decide(make_state(hp=10.0))
        assert action is not None
        assert healer.send(action, backend) is True
        # Direct re-send of the same action: cooldown active -> refused.
        assert healer.send(action, backend) is False
        assert len(backend.sent_lines) == 1

    def test_decide_and_send_end_to_end(self) -> None:
        combos = {"exura": "f2"}
        healer, _ = make_healer(spell_combos=combos)
        backend = FakeInput()
        action = healer.decide_and_send(make_state(hp=55.0), backend)
        assert action is not None
        assert action.type is HealType.NORMAL
        assert backend.sent_lines == ["K f2"]
        # Healthy state: no decision, nothing sent.
        assert healer.decide_and_send(make_state(hp=95.0), backend) is None
        assert len(backend.sent_lines) == 1

    def test_cure_sent_via_cure_combo(self) -> None:
        combos = {"exura gran": "f1"}
        healer, _ = make_healer(spell_combos=combos)
        backend = FakeInput()
        action = healer.decide_and_send(make_state(hp=90.0, buffs={"poison": 9.0}),
                                        backend)
        assert action is not None
        assert action.type is HealType.CURE
        assert backend.sent_lines == ["K f1"]


# -- config validation --------------------------------------------------------------


class TestConfigValidation:
    def test_default_config_is_valid(self) -> None:
        HealingConfig().validate()
        HealingDecision()  # must not raise

    @pytest.mark.parametrize(
        ("emergency", "normal"),
        [
            (30.0, 30.0),  # equal
            (40.0, 30.0),  # inverted
            (0.0, 70.0),  # zero
            (30.0, 101.0),  # > 100
            (-1.0, 70.0),
        ],
    )
    def test_invalid_thresholds_rejected(self, emergency: float, normal: float) -> None:
        with pytest.raises(ValueError):
            HealingDecision(
                HealingConfig(
                    emergency_hp_percent=emergency, normal_hp_percent=normal
                )
            )

    @pytest.mark.parametrize("mana", [-1.0, 101.0])
    def test_invalid_mana_threshold_rejected(self, mana: float) -> None:
        with pytest.raises(ValueError):
            HealingDecision(HealingConfig(mana_threshold=mana))

    def test_nonpositive_cooldown_rejected(self) -> None:
        with pytest.raises(ValueError):
            HealingDecision(HealingConfig(heal_cooldown_ms=0))

    @pytest.mark.parametrize("field", ["emergency_spell", "normal_spell"])
    @pytest.mark.parametrize("value", ["", "   "])
    def test_invalid_spells_rejected(self, field: str, value: str) -> None:
        # Dynamic field name: unpack through dict[str, Any] (intentionally invalid input).
        kwargs: dict[str, Any] = {field: value}
        with pytest.raises(ValueError):
            HealingDecision(HealingConfig(**kwargs))

    def test_invalid_spell_combo_rejected(self) -> None:
        with pytest.raises(ValueError):
            HealingDecision(spell_combos={"exura": "ctrl alt"})
        with pytest.raises(ValueError):
            HealingDecision(spell_combos={"exura": ""})


# -- HealingAction structure ---------------------------------------------------------


class TestHealingAction:
    def test_type_values(self) -> None:
        assert HealType.EMERGENCY.value == "emergency"
        assert HealType.NORMAL.value == "normal"
        assert HealType.CURE.value == "cure"

    def test_frozen(self) -> None:
        action = HealingAction(
            type=HealType.EMERGENCY,
            spell="exura gran",
            reason="hp 20% <= emergency threshold 30%",
            cooldown_key=EMERGENCY_COOLDOWN_KEY,
        )
        with pytest.raises(FrozenInstanceError):
            action.spell = "exura"  # type: ignore[misc]

    def test_cure_cooldown_key_format(self) -> None:
        assert cure_cooldown_key("paralyze") == "cure:paralyze"
        assert cure_cooldown_key("poison") != cure_cooldown_key("paralyze")
