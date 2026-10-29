"""Unit tests for moonlight.alarms.alarms (HP alarms, T25).

Fully hermetic: synthetic WorldState snapshots and an injectable fake clock.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from moonlight.alarms.alarms import AlarmConfig, AlarmDecision
from moonlight.core.state import WorldState


class FakeClock:
    """Deterministic monotonic clock (same pattern as test_loop.py)."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


def make_state(*, hp: float = 100.0) -> WorldState:
    state = WorldState.default()
    state.client_focused = True
    state.screen_ok = True
    state.hp_percent = hp
    state.last_update = 1.0
    return state


def make_alarms(
    config: AlarmConfig | None = None, clock: FakeClock | None = None
) -> tuple[AlarmDecision, FakeClock]:
    clk = clock if clock is not None else FakeClock()
    return AlarmDecision(config, clock=clk), clk


# -- config validation -------------------------------------------------------


class TestConfigValidation:
    def test_default_config_is_valid(self) -> None:
        AlarmConfig().validate()

    def test_both_alarms_are_on_by_default(self) -> None:
        cfg = AlarmConfig()
        assert cfg.low_hp_alarm is True
        assert cfg.fast_hp_drop_alarm is True

    @pytest.mark.parametrize("threshold", [0.0, -5.0, 150.0])
    def test_bad_fast_hp_drop_threshold(self, threshold: float) -> None:
        with pytest.raises(ValueError, match="fast_hp_drop_threshold"):
            AlarmConfig(fast_hp_drop_threshold=threshold).validate()

    @pytest.mark.parametrize("window", [0.0, -1.0])
    def test_bad_fast_hp_drop_window(self, window: float) -> None:
        with pytest.raises(ValueError, match="fast_hp_drop_window"):
            AlarmConfig(fast_hp_drop_window=window).validate()

    @pytest.mark.parametrize("threshold", [0.0, -10.0, 150.0])
    def test_bad_low_hp_threshold(self, threshold: float) -> None:
        with pytest.raises(ValueError, match="low_hp_threshold"):
            AlarmConfig(low_hp_threshold=threshold).validate()

    @pytest.mark.parametrize("size", [0, -3])
    def test_bad_hp_history_size(self, size: int) -> None:
        with pytest.raises(ValueError, match="hp_history_size"):
            AlarmConfig(hp_history_size=size).validate()

    @pytest.mark.parametrize("size", [2.5, True])
    def test_non_int_hp_history_size_rejected(self, size: object) -> None:
        # cast(): the runtime value is intentionally not an int; validate()
        # must reject it (bool is excluded even though it is an int subclass).
        cfg = replace(AlarmConfig(), hp_history_size=cast(int, size))
        with pytest.raises(ValueError, match="hp_history_size"):
            cfg.validate()

    def test_constructor_validates_config(self) -> None:
        with pytest.raises(ValueError):
            AlarmDecision(AlarmConfig(low_hp_threshold=0.0))

    def test_config_is_frozen(self) -> None:
        with pytest.raises(FrozenInstanceError):
            cfg = AlarmConfig()
            cfg.low_hp_alarm = False  # type: ignore[misc]


# -- decide() -----------------------------------------------------------------


class TestDecide:
    def test_healthy_world_no_alarm(self) -> None:
        alarms, clk = make_alarms()
        for _ in range(5):
            clk.advance(0.5)
            assert alarms.decide(make_state(hp=100.0)) is None

    def test_unknown_hp_no_alarm(self) -> None:
        alarms, _ = make_alarms()
        assert alarms.decide(make_state(hp=0.0)) is None

    def test_slow_hp_drop_no_alarm(self) -> None:
        alarms, clk = make_alarms()
        for hp in (100.0, 96.0, 92.0, 88.0, 84.0, 80.0):
            clk.advance(1.0)
            assert alarms.decide(make_state(hp=hp)) is None

    def test_low_hp(self) -> None:
        alarms, _ = make_alarms()
        assert alarms.decide(make_state(hp=19.0)) == "HP below 20%"

    def test_hp_at_the_threshold_is_fine(self) -> None:
        alarms, _ = make_alarms()
        assert alarms.decide(make_state(hp=20.0)) is None

    def test_low_hp_alarm_off(self) -> None:
        alarms, _ = make_alarms(AlarmConfig(low_hp_alarm=False))
        assert alarms.decide(make_state(hp=5.0)) is None

    def test_fast_hp_drop(self) -> None:
        alarms, clk = make_alarms()
        assert alarms.decide(make_state(hp=100.0)) is None
        clk.advance(0.5)
        assert alarms.decide(make_state(hp=85.0)) == "HP dropped 10+ points in 1 s"

    def test_fast_hp_drop_alarm_off(self) -> None:
        alarms, clk = make_alarms(AlarmConfig(fast_hp_drop_alarm=False))
        alarms.decide(make_state(hp=100.0))
        clk.advance(0.5)
        assert alarms.decide(make_state(hp=85.0)) is None

    def test_drop_wins_over_low_hp(self) -> None:
        alarms, clk = make_alarms()
        alarms.decide(make_state(hp=40.0))
        clk.advance(0.5)
        assert alarms.decide(make_state(hp=10.0)) == "HP dropped 10+ points in 1 s"

    def test_history_is_capped(self) -> None:
        alarms, clk = make_alarms(AlarmConfig(hp_history_size=3))
        for _ in range(10):
            clk.advance(0.1)
            alarms.decide(make_state())
        assert len(alarms._hp_history) == 3
