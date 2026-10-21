"""Unit tests for moonlight.decision.alarms (AlarmDecision, T25).

Fully hermetic: synthetic WorldState snapshots, an injectable fake clock
and injectable PM checks. No hardware, no client, no input backend (the
alarm module emits AlarmAction intents, it never sends input).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from typing import Any, cast

import pytest

from moonlight.bot.state import WorldState
from moonlight.decision.alarms import (
    ALARM_PRIORITY,
    AlarmAction,
    AlarmConfig,
    AlarmDecision,
    AlarmType,
    RecommendedAction,
    Severity,
)


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
    players_on_screen: int = 0,
) -> WorldState:
    """One consistent world-state frame (reader-style, like the loop gets)."""
    state = WorldState.default()
    state.client_focused = True
    state.screen_ok = True
    state.hp_percent = hp
    state.players_on_screen = players_on_screen
    state.last_update = 1.0
    return state


def make_alarms(
    config: AlarmConfig | None = None,
    clock: FakeClock | None = None,
    pm_open_check: Any | None = None,
) -> tuple[AlarmDecision, FakeClock]:
    clk = clock if clock is not None else FakeClock()
    alarms = AlarmDecision(config, clock=clk, pm_open_check=pm_open_check)
    return alarms, clk


# -- config validation -------------------------------------------------------


class TestConfigValidation:
    def test_default_config_is_valid(self) -> None:
        AlarmConfig().validate()

    def test_default_values_are_the_t25_spec(self) -> None:
        cfg = AlarmConfig()
        assert cfg.player_alarm is True
        assert cfg.fast_hp_drop_threshold == 10.0
        assert cfg.fast_hp_drop_window == 1.0
        assert cfg.low_hp_threshold == 20.0
        assert cfg.pm_alarm is True
        assert cfg.hp_history_size == 10

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
            cfg.player_alarm = False  # type: ignore[misc]


# -- decide(): no alarm -------------------------------------------------------


class TestDecideNoAlarm:
    def test_healthy_world_no_alarm(self) -> None:
        alarms, clk = make_alarms()
        for _ in range(5):
            clk.advance(0.5)
            assert alarms.decide(make_state(hp=100.0)) is None

    def test_unknown_hp_no_alarm(self) -> None:
        alarms, _ = make_alarms()
        assert alarms.decide(make_state(hp=0.0)) is None

    def test_slow_hp_drop_no_alarm(self) -> None:
        # 4 points over 4 seconds is well under the 10/1s threshold.
        alarms, clk = make_alarms()
        for hp in (100.0, 96.0, 92.0, 88.0, 84.0, 80.0):
            clk.advance(1.0)
            assert alarms.decide(make_state(hp=hp)) is None

    def test_player_alarm_disabled(self) -> None:
        alarms, _ = make_alarms(AlarmConfig(player_alarm=False))
        assert alarms.decide(make_state(players_on_screen=2)) is None

    def test_pm_alarm_disabled(self) -> None:
        alarms, _ = make_alarms(
            AlarmConfig(pm_alarm=False),
            pm_open_check=lambda state: True,
        )
        assert alarms.decide(make_state()) is None

    def test_no_pm_check_never_alarms(self) -> None:
        alarms, _ = make_alarms()
        assert alarms.decide(make_state()) is None


# -- player alarm -------------------------------------------------------------


class TestPlayerAlarm:
    def test_player_on_screen(self) -> None:
        alarms, _ = make_alarms()
        action = alarms.decide(make_state(players_on_screen=1))
        assert action is not None
        assert action.type is AlarmType.PLAYER_DETECTED
        assert action.severity is Severity.WARNING
        assert action.recommended_action is RecommendedAction.FLEE
        assert "1 player(s) on screen" in action.message

    def test_multiple_players_counted(self) -> None:
        alarms, _ = make_alarms()
        action = alarms.decide(make_state(players_on_screen=3))
        assert action is not None
        assert "3 player(s) on screen" in action.message

    def test_no_players_no_alarm(self) -> None:
        alarms, _ = make_alarms()
        assert alarms.decide(make_state(players_on_screen=0)) is None


# -- pm alarm ------------------------------------------------------------------


class TestPmAlarm:
    def test_pm_open(self) -> None:
        alarms, _ = make_alarms(pm_open_check=lambda state: True)
        action = alarms.decide(make_state())
        assert action is not None
        assert action.type is AlarmType.PM_OPEN
        assert action.severity is Severity.WARNING
        assert action.recommended_action is RecommendedAction.ALERT_USER
        assert action.message == "PM window open"

    def test_pm_closed_no_alarm(self) -> None:
        alarms, _ = make_alarms(pm_open_check=lambda state: False)
        assert alarms.decide(make_state()) is None

    def test_check_receives_the_state(self) -> None:
        seen: list[WorldState] = []

        def check(state: WorldState) -> bool:
            seen.append(state)
            return True

        alarms, _ = make_alarms(pm_open_check=check)
        state = make_state()
        alarms.decide(state)
        assert len(seen) == 1
        assert seen[0] is state


# -- low hp alarm ----------------------------------------------------------------


class TestLowHpAlarm:
    def test_hp_below_threshold(self) -> None:
        alarms, _ = make_alarms()
        action = alarms.decide(make_state(hp=15.0))
        assert action is not None
        assert action.type is AlarmType.LOW_HP
        assert action.severity is Severity.CRITICAL
        assert action.recommended_action is RecommendedAction.HEAL
        assert "15%" in action.message
        assert "20%" in action.message

    def test_hp_at_threshold_no_alarm(self) -> None:
        # Strictly below only: exactly at the threshold is not low HP yet.
        alarms, _ = make_alarms()
        assert alarms.decide(make_state(hp=20.0)) is None

    def test_hp_above_threshold_no_alarm(self) -> None:
        alarms, _ = make_alarms()
        assert alarms.decide(make_state(hp=25.0)) is None

    def test_unknown_hp_no_alarm(self) -> None:
        alarms, _ = make_alarms()
        assert alarms.decide(make_state(hp=0.0)) is None

    def test_custom_threshold(self) -> None:
        alarms, _ = make_alarms(AlarmConfig(low_hp_threshold=50.0))
        action = alarms.decide(make_state(hp=49.9))
        assert action is not None
        assert action.type is AlarmType.LOW_HP
        assert alarms.decide(make_state(hp=50.0)) is None


# -- fast hp drop alarm -----------------------------------------------------------


class TestFastHpDropAlarm:
    def test_first_sample_no_alarm(self) -> None:
        alarms, _ = make_alarms()
        assert alarms.decide(make_state(hp=37.0)) is None

    def test_stable_hp_no_alarm(self) -> None:
        alarms, clk = make_alarms()
        for _ in range(10):
            clk.advance(0.5)
            assert alarms.decide(make_state(hp=90.0)) is None

    def test_fast_drop_alarms(self) -> None:
        alarms, clk = make_alarms()
        alarms.decide(make_state(hp=95.0))  # t=0.0
        clk.advance(0.8)
        action = alarms.decide(make_state(hp=83.0))  # -12 within the window
        assert action is not None
        assert action.type is AlarmType.FAST_HP_DROP
        assert action.severity is Severity.CRITICAL
        assert action.recommended_action is RecommendedAction.HEAL
        assert "12.0" in action.message

    def test_drop_just_below_threshold_no_alarm(self) -> None:
        alarms, clk = make_alarms()
        alarms.decide(make_state(hp=90.0))
        clk.advance(0.8)
        assert alarms.decide(make_state(hp=80.5)) is None  # -9.5 < 10

    def test_drop_exactly_at_threshold_alarms(self) -> None:
        alarms, clk = make_alarms()
        alarms.decide(make_state(hp=90.0))
        clk.advance(0.8)
        action = alarms.decide(make_state(hp=80.0))  # -10 == threshold
        assert action is not None
        assert action.type is AlarmType.FAST_HP_DROP

    def test_drop_outside_window_no_alarm(self) -> None:
        # -15 points, but spread over 3s: only ~5 points in the last 1s.
        alarms, clk = make_alarms()
        for hp, t in ((90.0, 0.0), (85.0, 1.0), (80.0, 2.0), (75.0, 3.0)):
            if t > 0:
                clk.advance(1.0)
            assert alarms.decide(make_state(hp=hp)) is None

    def test_comparison_uses_reading_from_window_ago(self) -> None:
        # 100 at t=0, 99 at t=0.9, 80 at t=2.0: within the last second
        # the drop is only 99 -> 80 = 19 >= 10 -> alarm, and the
        # reference is the reading from ~1s ago (99), not t=0 (100).
        alarms, clk = make_alarms()
        alarms.decide(make_state(hp=100.0))
        clk.advance(0.9)
        alarms.decide(make_state(hp=99.0))
        clk.advance(1.1)
        action = alarms.decide(make_state(hp=80.0))
        assert action is not None
        assert action.type is AlarmType.FAST_HP_DROP
        assert "19.0" in action.message

    def test_unknown_hp_skipped_in_history(self) -> None:
        alarms, clk = make_alarms()
        alarms.decide(make_state(hp=90.0))
        assert alarms.hp_history_size == 1
        clk.advance(0.5)
        alarms.decide(make_state(hp=0.0))  # unknown: not recorded
        assert alarms.hp_history_size == 1

    def test_custom_threshold_and_window(self) -> None:
        cfg = AlarmConfig(fast_hp_drop_threshold=30.0, fast_hp_drop_window=2.0)
        alarms, clk = make_alarms(cfg)
        alarms.decide(make_state(hp=80.0))
        clk.advance(1.5)
        assert alarms.decide(make_state(hp=55.0)) is None  # -25 < 30
        clk.advance(0.5)
        action = alarms.decide(make_state(hp=50.0))  # -30 == 30 within 2s
        assert action is not None
        assert action.type is AlarmType.FAST_HP_DROP


# -- hp history bookkeeping ---------------------------------------------------------


class TestHpHistory:
    def test_history_grows_with_known_readings(self) -> None:
        alarms, clk = make_alarms()
        for _ in range(5):
            clk.advance(0.1)
            alarms.decide(make_state(hp=80.0))
        assert alarms.hp_history_size == 5

    def test_history_is_bounded(self) -> None:
        alarms, clk = make_alarms(AlarmConfig(hp_history_size=3))
        for _ in range(10):
            clk.advance(0.1)
            alarms.decide(make_state(hp=80.0))
        assert alarms.hp_history_size == 3

    def test_reset_history(self) -> None:
        alarms, _ = make_alarms()
        alarms.decide(make_state(hp=80.0))
        assert alarms.hp_history_size == 1
        alarms.reset_history()
        assert alarms.hp_history_size == 0

    def test_uses_injected_clock_for_samples(self) -> None:
        clk = FakeClock()
        clk.now = 42.0
        alarms, _ = make_alarms(clock=clk)
        alarms.decide(make_state(hp=80.0))
        # Reference selection depends on sample timestamps: the sample at
        # t=42 only becomes the ~1s-old reference once the clock passes
        # 43.0, and the reported span must reflect the injected clock.
        clk.advance(1.0)
        action = alarms.decide(make_state(hp=70.0))  # -10 vs the t=42 sample
        assert action is not None
        assert action.type is AlarmType.FAST_HP_DROP
        assert "1.00s" in action.message


# -- priority ordering ------------------------------------------------------------


class TestPriorityOrdering:
    def test_priority_constant_order(self) -> None:
        assert ALARM_PRIORITY == (
            AlarmType.FAST_HP_DROP,
            AlarmType.LOW_HP,
            AlarmType.PLAYER_DETECTED,
            AlarmType.PM_OPEN,
        )

    def test_fast_drop_beats_player(self) -> None:
        alarms, clk = make_alarms()
        alarms.decide(make_state(hp=95.0, players_on_screen=1))
        clk.advance(0.8)
        action = alarms.decide(make_state(hp=80.0, players_on_screen=1))
        assert action is not None
        assert action.type is AlarmType.FAST_HP_DROP

    def test_fast_drop_beats_pm(self) -> None:
        alarms, clk = make_alarms(pm_open_check=lambda state: True)
        alarms.decide(make_state(hp=95.0))
        clk.advance(0.8)
        action = alarms.decide(make_state(hp=80.0))
        assert action is not None
        assert action.type is AlarmType.FAST_HP_DROP

    def test_low_hp_beats_player(self) -> None:
        alarms, _ = make_alarms()
        action = alarms.decide(make_state(hp=15.0, players_on_screen=2))
        assert action is not None
        assert action.type is AlarmType.LOW_HP

    def test_low_hp_beats_pm(self) -> None:
        alarms, _ = make_alarms(pm_open_check=lambda state: True)
        action = alarms.decide(make_state(hp=15.0))
        assert action is not None
        assert action.type is AlarmType.LOW_HP

    def test_player_beats_pm(self) -> None:
        alarms, _ = make_alarms(pm_open_check=lambda state: True)
        action = alarms.decide(make_state(players_on_screen=1))
        assert action is not None
        assert action.type is AlarmType.PLAYER_DETECTED

    def test_only_pm_last(self) -> None:
        alarms, _ = make_alarms(pm_open_check=lambda state: True)
        action = alarms.decide(make_state())
        assert action is not None
        assert action.type is AlarmType.PM_OPEN


# -- action structure -------------------------------------------------------------


class TestAlarmAction:
    def test_action_is_frozen(self) -> None:
        action = AlarmAction(
            type=AlarmType.PM_OPEN,
            severity=Severity.WARNING,
            message="PM window open",
            recommended_action=RecommendedAction.ALERT_USER,
        )
        with pytest.raises(FrozenInstanceError):
            action.message = "tampered"  # type: ignore[misc]

    def test_type_tokens_match_spec(self) -> None:
        assert {t.value for t in AlarmType} == {
            "fast_hp_drop",
            "low_hp",
            "player_detected",
            "pm_open",
        }

    def test_severity_tokens_match_spec(self) -> None:
        assert {s.value for s in Severity} == {"warning", "critical"}

    def test_recommended_action_tokens_match_spec(self) -> None:
        assert {a.value for a in RecommendedAction} == {
            "flee",
            "heal",
            "stop",
            "alert_user",
        }

    def test_severity_and_recommendation_per_type(self) -> None:
        expected = {
            AlarmType.FAST_HP_DROP: (Severity.CRITICAL, RecommendedAction.HEAL),
            AlarmType.LOW_HP: (Severity.CRITICAL, RecommendedAction.HEAL),
            AlarmType.PLAYER_DETECTED: (Severity.WARNING, RecommendedAction.FLEE),
            AlarmType.PM_OPEN: (Severity.WARNING, RecommendedAction.ALERT_USER),
        }
        # fast_hp_drop: 95 -> 80 within the window.
        alarms, clk = make_alarms()
        alarms.decide(make_state(hp=95.0))
        clk.advance(0.8)
        self._assert_profile(AlarmType.FAST_HP_DROP, alarms.decide(make_state(hp=80.0)), expected)
        # low_hp: 10% < 20%.
        alarms, _ = make_alarms()
        self._assert_profile(AlarmType.LOW_HP, alarms.decide(make_state(hp=10.0)), expected)
        # player_detected: one player on screen.
        alarms, _ = make_alarms()
        self._assert_profile(
            AlarmType.PLAYER_DETECTED, alarms.decide(make_state(players_on_screen=1)), expected
        )
        # pm_open: PM check reports open.
        alarms, _ = make_alarms(pm_open_check=lambda state: True)
        self._assert_profile(AlarmType.PM_OPEN, alarms.decide(make_state()), expected)

    @staticmethod
    def _assert_profile(
        alarm_type: AlarmType,
        action: AlarmAction | None,
        expected: dict[AlarmType, tuple[Severity, RecommendedAction]],
    ) -> None:
        assert action is not None
        severity, recommended = expected[alarm_type]
        assert action.type is alarm_type
        assert action.severity is severity
        assert action.recommended_action is recommended
