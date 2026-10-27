"""Unit tests for moonlight.extras.buffs (BuffManager, T26).

Fully hermetic: synthetic WorldState snapshots, an injectable fake clock,
and FakeInput for the input-backend integration. No hardware, no client.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from moonlight.core.state import WorldState
from moonlight.extras.buffs import (
    DEFAULT_BUFFS,
    REASON_EXPIRING_SOON,
    REASON_MISSING,
    BuffAction,
    BuffConfig,
    BuffManager,
    BuffManagerConfig,
    buff_cooldown_key,
)
from moonlight.hardware.input import FakeInput


class FakeClock:
    """Deterministic monotonic clock (same pattern as test_healing.py)."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


# Fixed entries used across the tests: speed (required, p10), fire
# protection (required, p20), luck (optional, p30).
SPEED = BuffConfig(buff_name="speed", spell="utego", priority=10)
FIRE = BuffConfig(buff_name="fire_protection", spell="exori flamatio", priority=20)
LUCK = BuffConfig(buff_name="luck", spell="anemo", priority=30, required=False)


def make_state(buffs: dict[str, float] | None = None) -> WorldState:
    """One consistent world-state frame (reader-style, like the loop gets)."""
    state = WorldState.default()
    state.client_focused = True
    state.screen_ok = True
    state.last_update = 1.0
    if buffs is not None:
        state.buffs = dict(buffs)
    return state


def make_manager(
    config: BuffManagerConfig | None = None,
    clock: FakeClock | None = None,
    key_combos: dict[str, str] | None = None,
) -> tuple[BuffManager, FakeClock]:
    clk = clock if clock is not None else FakeClock()
    manager = BuffManager(
        config,
        clock=clk,
        key_combos=key_combos if key_combos is not None else {},
    )
    return manager, clk


def standard_config() -> BuffManagerConfig:
    return BuffManagerConfig(buffs=[SPEED, FIRE, LUCK])


# -- config validation -----------------------------------------------------------


class TestConfigValidation:
    def test_default_config_is_valid(self) -> None:
        BuffManagerConfig().validate()
        BuffManager()  # must not raise

    def test_default_buffs(self) -> None:
        # F-10: the previous defaults were wrong (exori * are attacks, not
        # buffs; 'utemo' is not a real spell). Until buff-icon perception is
        # wired (T34) the default list is empty and the module defaults to OFF.
        assert DEFAULT_BUFFS == ()
        assert BuffManagerConfig().buffs == []

    @pytest.mark.parametrize("cooldown", [0, -100])
    def test_nonpositive_cooldown_rejected(self, cooldown: int) -> None:
        with pytest.raises(ValueError):
            BuffManager(BuffManagerConfig(cast_cooldown_ms=cooldown))

    def test_negative_warning_rejected(self) -> None:
        with pytest.raises(ValueError):
            BuffManager(BuffManagerConfig(expiring_warning_ms=-1))

    def test_zero_warning_allowed(self) -> None:
        BuffManager(BuffManagerConfig(expiring_warning_ms=0))

    def test_duplicate_buff_names_rejected(self) -> None:
        with pytest.raises(ValueError):
            BuffManager(
                BuffManagerConfig(
                    buffs=[SPEED, BuffConfig(buff_name="Speed", spell="x", priority=11)]
                )
            )

    @pytest.mark.parametrize("name", ["", "a b"])
    def test_invalid_buff_name_rejected(self, name: str) -> None:
        with pytest.raises(ValueError):
            BuffManager(
                BuffManagerConfig(
                    buffs=[BuffConfig(buff_name=name, spell="x", priority=1)]
                )
            )

    @pytest.mark.parametrize("value", ["", "   "])
    def test_empty_spell_rejected(self, value: str) -> None:
        with pytest.raises(ValueError):
            BuffManager(
                BuffManagerConfig(
                    buffs=[BuffConfig(buff_name="speed", spell=value, priority=1)]
                )
            )

    def test_invalid_spell_combo_rejected(self) -> None:
        with pytest.raises(ValueError):
            BuffManager(key_combos={"utego": "ctrl alt"})
        with pytest.raises(ValueError):
            BuffManager(key_combos={"utego": ""})


# -- track(): missing buffs ------------------------------------------------------


class TestTrackMissing:
    def test_all_required_missing_returns_all_in_priority_order(self) -> None:
        manager, _ = make_manager(BuffManagerConfig(buffs=[SPEED, FIRE]))
        actions = manager.track(make_state(buffs={}))
        assert [a.buff_name for a in actions] == ["speed", "fire_protection"]
        assert all(a.reason == REASON_MISSING for a in actions)
        assert [a.priority for a in actions] == [10, 20]

    def test_action_fields(self) -> None:
        manager, _ = make_manager(BuffManagerConfig(buffs=[FIRE]))
        actions = manager.track(make_state(buffs={}))
        assert len(actions) == 1
        assert actions[0].spell == "exori flamatio"
        assert actions[0].buff_name == "fire_protection"
        assert actions[0].reason == REASON_MISSING
        assert actions[0].priority == 20

    def test_all_active_no_action(self) -> None:
        manager, _ = make_manager(standard_config())
        state = make_state(buffs={"speed": 60.0, "fire_protection": 180.0, "luck": 90.0})
        assert manager.track(state) == []

    def test_case_insensitive_match(self) -> None:
        manager, _ = make_manager(standard_config())
        state = make_state(
            buffs={"Speed": 60.0, "Fire_Protection": 180.0, "Luck": 90.0}
        )
        assert manager.track(state) == []

    def test_unrelated_buff_ignored(self) -> None:
        manager, _ = make_manager(standard_config())
        actions = manager.track(make_state(buffs={"haste": 30.0}))
        # "haste" is not a configured buff; the required casts are offered,
        # the optional one waits for the required set to be active.
        assert [a.buff_name for a in actions] == ["speed", "fire_protection"]

    def test_empty_config_empty_result(self) -> None:
        manager, _ = make_manager(BuffManagerConfig(buffs=[]))
        assert manager.track(make_state(buffs={})) == []

    def test_default_config_tracks_no_buffs(self) -> None:
        # F-10: the default buff list is empty, so a default config tracks
        # nothing until buffs are configured (and buff-icon perception wired).
        manager, _ = make_manager()
        assert manager.track(make_state(buffs={})) == []


# -- track(): expiring soon -------------------------------------------------------


class TestTrackExpiring:
    def test_expiring_within_warning(self) -> None:
        manager, _ = make_manager(BuffManagerConfig(buffs=[SPEED]))
        state = make_state(buffs={"speed": 4.9})  # warning 5000 ms
        actions = manager.track(state)
        assert len(actions) == 1
        assert actions[0].reason == REASON_EXPIRING_SOON

    def test_boundary_at_warning_expires(self) -> None:
        manager, _ = make_manager(BuffManagerConfig(buffs=[SPEED]))
        state = make_state(buffs={"speed": 5.0})  # exactly 5000 ms
        actions = manager.track(state)
        assert len(actions) == 1
        assert actions[0].reason == REASON_EXPIRING_SOON

    def test_just_above_warning_no_action(self) -> None:
        manager, _ = make_manager(BuffManagerConfig(buffs=[SPEED]))
        state = make_state(buffs={"speed": 5.1})
        assert manager.track(state) == []

    def test_zero_remaining_expires(self) -> None:
        manager, _ = make_manager(BuffManagerConfig(buffs=[SPEED]))
        state = make_state(buffs={"speed": 0.0})
        actions = manager.track(state)
        assert len(actions) == 1
        assert actions[0].reason == REASON_EXPIRING_SOON

    def test_negative_remaining_expires(self) -> None:
        manager, _ = make_manager(BuffManagerConfig(buffs=[SPEED]))
        state = make_state(buffs={"speed": -2.0})
        actions = manager.track(state)
        assert len(actions) == 1
        assert actions[0].reason == REASON_EXPIRING_SOON

    def test_custom_warning_respected(self) -> None:
        config = BuffManagerConfig(buffs=[SPEED], expiring_warning_ms=1000)
        manager, _ = make_manager(config)
        # 1.1s remaining > 1s warning -> no recast yet.
        assert manager.track(make_state(buffs={"speed": 1.1})) == []
        # 1.0s remaining == warning -> recast (boundary inclusive).
        actions = manager.track(make_state(buffs={"speed": 1.0}))
        assert len(actions) == 1
        assert actions[0].reason == REASON_EXPIRING_SOON

    def test_expiring_and_missing_coexist_in_result(self) -> None:
        manager, _ = make_manager(BuffManagerConfig(buffs=[SPEED, FIRE]))
        state = make_state(buffs={"speed": 2.0})  # fire missing entirely
        by_name = {a.buff_name: a for a in manager.track(state)}
        assert by_name["speed"].reason == REASON_EXPIRING_SOON
        assert by_name["fire_protection"].reason == REASON_MISSING


# -- track(): priority ordering ----------------------------------------------------


class TestPriorityOrdering:
    def test_out_of_order_config_is_sorted(self) -> None:
        config = BuffManagerConfig(buffs=[FIRE, SPEED])
        manager, _ = make_manager(config)
        actions = manager.track(make_state(buffs={}))
        assert [a.priority for a in actions] == [10, 20]
        assert [a.buff_name for a in actions] == ["speed", "fire_protection"]

    def test_equal_priorities_keep_list_order(self) -> None:
        a = BuffConfig(buff_name="alpha", spell="s1", priority=10)
        b = BuffConfig(buff_name="beta", spell="s2", priority=10)
        manager, _ = make_manager(BuffManagerConfig(buffs=[b, a]))
        actions = manager.track(make_state(buffs={}))
        assert [x.buff_name for x in actions] == ["beta", "alpha"]

    def test_negative_priority_ranks_first(self) -> None:
        hi = BuffConfig(buff_name="hi", spell="s1", priority=-5)
        lo = BuffConfig(buff_name="lo", spell="s2", priority=100)
        manager, _ = make_manager(BuffManagerConfig(buffs=[lo, hi]))
        actions = manager.track(make_state(buffs={}))
        assert [x.buff_name for x in actions] == ["hi", "lo"]

    def test_optional_uses_its_own_priority_when_considered(self) -> None:
        luck_first = BuffConfig(buff_name="luck", spell="anemo", priority=5, required=False)
        config = BuffManagerConfig(buffs=[SPEED, luck_first, FIRE])
        manager, _ = make_manager(config)
        state = make_state(buffs={"speed": 60.0, "fire_protection": 180.0})
        actions = manager.track(state)
        assert [a.buff_name for a in actions] == ["luck"]
        assert actions[0].priority == 5


# -- track(): required vs optional -------------------------------------------------


class TestRequiredOptional:
    def test_optional_recast_when_required_active(self) -> None:
        manager, _ = make_manager(standard_config())
        state = make_state(buffs={"speed": 60.0, "fire_protection": 180.0})
        actions = manager.track(state)
        assert [a.buff_name for a in actions] == ["luck"]
        assert actions[0].reason == REASON_MISSING

    def test_optional_skipped_when_required_missing(self) -> None:
        manager, _ = make_manager(standard_config())
        state = make_state(buffs={"fire_protection": 180.0})
        actions = manager.track(state)
        assert [a.buff_name for a in actions] == ["speed"]

    def test_optional_skipped_when_all_missing(self) -> None:
        # Required buffs are secured first; the optional one joins once the
        # required set is active.
        manager, _ = make_manager(standard_config())
        actions = manager.track(make_state(buffs={}))
        assert [a.buff_name for a in actions] == ["speed", "fire_protection"]

    def test_optional_considered_if_required_about_to_expire(self) -> None:
        # Presence-based gating: speed is still *active* (2s > 0), so the
        # optional buff is considered in the same result set.
        manager, _ = make_manager(standard_config())
        state = make_state(buffs={"speed": 2.0, "fire_protection": 180.0})
        actions = manager.track(state)
        assert [a.buff_name for a in actions] == ["speed", "luck"]
        assert actions[0].reason == REASON_EXPIRING_SOON
        assert actions[1].reason == REASON_MISSING

    def test_all_optional_config(self) -> None:
        # Vacuous required set: optional buffs are maintained normally.
        manager, _ = make_manager(BuffManagerConfig(buffs=[LUCK]))
        actions = manager.track(make_state(buffs={}))
        assert len(actions) == 1
        assert actions[0].buff_name == "luck"


# -- cooldowns --------------------------------------------------------------------


class TestCooldowns:
    def test_no_cooldown_initially(self) -> None:
        manager, _ = make_manager(standard_config())
        assert not manager.on_cooldown(buff_cooldown_key("speed"))
        assert manager.cooldown_remaining_ms(buff_cooldown_key("speed")) == 0.0

    def test_cooldown_blocks_immediate_repeat(self) -> None:
        combos = {"utego": "f3", "exori flamatio": "f4"}
        manager, _ = make_manager(standard_config(), key_combos=combos)
        backend = FakeInput()
        # First tick: speed cast sent.
        assert manager.track_and_send(make_state(buffs={}), backend) is not None
        # Same instant: speed on cooldown -> only fire protection offered.
        actions = manager.track(make_state(buffs={}))
        assert [a.buff_name for a in actions] == ["fire_protection"]
        assert len(backend.sent_lines) == 1

    def test_cooldown_expires(self) -> None:
        combos = {"utego": "f3"}
        clk = FakeClock()
        manager = BuffManager(
            BuffManagerConfig(buffs=[SPEED], cast_cooldown_ms=500),
            clock=clk,
            key_combos=combos,
        )
        backend = FakeInput()
        manager.track_and_send(make_state(buffs={}), backend)
        clk.advance(0.4)
        assert manager.on_cooldown(buff_cooldown_key("speed"))
        assert manager.track(make_state(buffs={})) == []
        clk.advance(0.11)
        assert not manager.on_cooldown(buff_cooldown_key("speed"))
        assert len(manager.track(make_state(buffs={}))) == 1
        assert len(backend.sent_lines) == 1

    def test_cooldown_keys_are_independent(self) -> None:
        combos = {"utego": "f3", "exori flamatio": "f4"}
        manager, _ = make_manager(standard_config(), key_combos=combos)
        backend = FakeInput()
        manager.track_and_send(make_state(buffs={}), backend)  # speed sent
        # Speed is on cooldown, but fire protection's own key is free ->
        # it is still offered.
        assert manager.on_cooldown(buff_cooldown_key("speed"))
        assert not manager.on_cooldown(buff_cooldown_key("fire_protection"))
        actions = manager.track(make_state(buffs={}))
        assert [a.buff_name for a in actions] == ["fire_protection"]

    def test_send_records_cooldown(self) -> None:
        combos = {"utego": "f3"}
        clk = FakeClock()
        manager = BuffManager(
            BuffManagerConfig(buffs=[SPEED], cast_cooldown_ms=1000),
            clock=clk,
            key_combos=combos,
        )
        backend = FakeInput()
        action = manager.track(make_state(buffs={}))[0]
        assert manager.send(action, backend) is True
        assert manager.on_cooldown(buff_cooldown_key("speed"))
        remaining = manager.cooldown_remaining_ms(buff_cooldown_key("speed"))
        assert 0.0 < remaining <= 1000.0
        clk.advance(remaining / 1000.0 + 0.001)
        assert not manager.on_cooldown(buff_cooldown_key("speed"))

    def test_reset_cooldowns(self) -> None:
        manager, clk = make_manager(standard_config())
        clk.advance(0.1)
        manager.reset_cooldowns()
        manager._last_sent[buff_cooldown_key("speed")] = clk()  # direct bookkeeping
        assert manager.on_cooldown(buff_cooldown_key("speed"))
        manager.reset_cooldowns()
        assert not manager.on_cooldown(buff_cooldown_key("speed"))


# -- send(): input integration -----------------------------------------------------


class TestSend:
    def test_send_with_combo_mapping(self) -> None:
        combos = {"exori flamatio": "f4"}
        manager, _ = make_manager(BuffManagerConfig(buffs=[FIRE]), key_combos=combos)
        backend = FakeInput()
        action = manager.track(make_state(buffs={}))[0]
        assert manager.send(action, backend) is True
        assert backend.sent_lines == ["K f4"]
        assert backend.last_reply == "OK"

    def test_send_without_combo_mapping_declines(self) -> None:
        manager, _ = make_manager(BuffManagerConfig(buffs=[SPEED]), key_combos={})
        backend = FakeInput()
        action = manager.track(make_state(buffs={}))[0]
        assert manager.send(action, backend) is False
        assert backend.sent_lines == []

    def test_send_refuses_while_on_cooldown(self) -> None:
        combos = {"utego": "f3"}
        manager, _ = make_manager(BuffManagerConfig(buffs=[SPEED]), key_combos=combos)
        backend = FakeInput()
        action = manager.track(make_state(buffs={}))[0]
        assert manager.send(action, backend) is True
        # Direct re-send of the same action: cooldown active -> refused.
        assert manager.send(action, backend) is False
        assert len(backend.sent_lines) == 1

    def test_track_and_send_end_to_end(self) -> None:
        combos = {"utego": "f3", "exori flamatio": "f4"}
        manager, _ = make_manager(standard_config(), key_combos=combos)
        backend = FakeInput()
        action = manager.track_and_send(make_state(buffs={}), backend)
        assert action is not None
        assert action.buff_name == "speed"
        assert backend.sent_lines == ["K f3"]
        # Everything active: no decision, nothing sent.
        state = make_state(buffs={"speed": 60.0, "fire_protection": 180.0, "luck": 90.0})
        assert manager.track_and_send(state, backend) is None
        assert len(backend.sent_lines) == 1

    def test_track_and_send_sends_highest_priority_only(self) -> None:
        combos = {"utego": "f3", "exori flamatio": "f4", "anemo": "f5"}
        manager, _ = make_manager(standard_config(), key_combos=combos)
        backend = FakeInput()
        manager.track_and_send(make_state(buffs={}), backend)
        assert backend.sent_lines == ["K f3"]  # speed, not fire/luck


# -- BuffAction structure -----------------------------------------------------------


class TestBuffAction:
    def test_reason_tokens(self) -> None:
        assert REASON_MISSING == "missing"
        assert REASON_EXPIRING_SOON == "expiring_soon"

    def test_frozen(self) -> None:
        action = BuffAction(
            spell="utego", buff_name="speed", reason="missing", priority=10
        )
        with pytest.raises(FrozenInstanceError):
            action.spell = "utemo"  # type: ignore[misc]

    def test_buff_cooldown_key_format(self) -> None:
        assert buff_cooldown_key("speed") == "buff:speed"
        assert buff_cooldown_key("Speed") == buff_cooldown_key("speed")
        assert buff_cooldown_key("speed") != buff_cooldown_key("luck")


class TestIntegrationScenarios:
    """Scenario-style tests for the full recast cycle."""

    def test_recast_cycle(self) -> None:
        combos = {"utego": "f3"}
        clk = FakeClock()
        manager = BuffManager(
            BuffManagerConfig(buffs=[SPEED], cast_cooldown_ms=1000),
            clock=clk,
            key_combos=combos,
        )
        backend = FakeInput()

        # 1. Buff missing -> cast it.
        action = manager.track_and_send(make_state(buffs={}), backend)
        assert action is not None and action.reason == REASON_MISSING

        # 2. Buff active with plenty of time -> nothing to do.
        state = make_state(buffs={"speed": 120.0})
        assert manager.track_and_send(state, backend) is None
        assert len(backend.sent_lines) == 1

        # 3. Buff about to expire -> recast (after the cooldown elapses).
        clk.advance(1.5)
        state = make_state(buffs={"speed": 3.0})
        action = manager.track_and_send(state, backend)
        assert action is not None and action.reason == REASON_EXPIRING_SOON
        assert len(backend.sent_lines) == 2
