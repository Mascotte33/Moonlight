"""Alarm decisions (T25).

Detects dangerous situations and turns them into alarm actions, given
:class:`moonlight.state.WorldState`. In the loop's priority state machine
ALARM is priority 2 (below KILL, above EMERGENCY_HEAL): an alarm preempts
attack/loot/walk while healing continues.

Alarm conditions (checked in :data:`ALARM_PRIORITY` order, first hit wins):

* **fast_hp_drop** (critical, -> heal): HP fell by
  ``fast_hp_drop_threshold`` points within roughly
  ``fast_hp_drop_window`` seconds. The decision engine tracks its own
  rolling HP history (last ``hp_history_size`` readings).
* **low_hp** (critical, -> heal): known HP is strictly below
  ``low_hp_threshold`` percent.
* **player_detected** (warning, -> flee): a player is on screen
  (``WorldState.players_on_screen > 0``), when ``player_alarm`` is on.
* **pm_open** (warning, -> alert_user): a private-message window is open.
  PM windows are not part of ``WorldState`` yet, so detection is an
  injectable ``pm_open_check`` (perception/UI provides it); without one
  the PM alarm never fires (it is not detectable).

Conventions inherited from the rest of the pipeline:

* ``hp_percent`` of 0.0 means "unknown" (never read): no HP-based alarm
  and the reading is excluded from the HP history.
* ``client_focused`` / ``screen_ok`` gating is the loop's job (it refuses
  to act without both), so this module does not check them.
* Everything timing-related is injectable (``clock``), so the module is
  fully unit-testable without hardware or a running client.

:class:`AlarmDecision` is a decision engine plus HP-history bookkeeping;
it never talks to the game itself (the loop/UI consumes the
:class:`AlarmAction` and raises the actual sound/UI alarm).

Thread-safety contract: a single decision thread (the 20 Hz loop) drives
this class; there is no internal locking (same as GameLoop).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from moonlight.state import WorldState

__all__ = [
    "ALARM_PRIORITY",
    "AlarmAction",
    "AlarmConfig",
    "AlarmDecision",
    "AlarmType",
    "RecommendedAction",
    "Severity",
]

logger = logging.getLogger("moonlight.decision.alarms")


class AlarmType(StrEnum):
    """Kind of alarm (the ``AlarmAction.type`` tokens)."""

    FAST_HP_DROP = "fast_hp_drop"
    LOW_HP = "low_hp"
    PLAYER_DETECTED = "player_detected"
    PM_OPEN = "pm_open"


class Severity(StrEnum):
    """How urgent an alarm is (the ``AlarmAction.severity`` tokens)."""

    WARNING = "warning"
    CRITICAL = "critical"


class RecommendedAction(StrEnum):
    """What the loop/UI should do about an alarm (``recommended_action`` tokens)."""

    FLEE = "flee"
    HEAL = "heal"
    STOP = "stop"
    ALERT_USER = "alert_user"


#: Priority ordering of alarm types, highest first: life-threatening HP
#: alarms beat situational alarms (players, PMs). ``decide()`` returns
#: the first matching condition in this order.
ALARM_PRIORITY: tuple[AlarmType, ...] = (
    AlarmType.FAST_HP_DROP,
    AlarmType.LOW_HP,
    AlarmType.PLAYER_DETECTED,
    AlarmType.PM_OPEN,
)

#: Severity and recommended action for each alarm type.
_ALARM_PROFILES: dict[AlarmType, tuple[Severity, RecommendedAction]] = {
    AlarmType.FAST_HP_DROP: (Severity.CRITICAL, RecommendedAction.HEAL),
    AlarmType.LOW_HP: (Severity.CRITICAL, RecommendedAction.HEAL),
    AlarmType.PLAYER_DETECTED: (Severity.WARNING, RecommendedAction.FLEE),
    AlarmType.PM_OPEN: (Severity.WARNING, RecommendedAction.ALERT_USER),
}


@dataclass(frozen=True)
class AlarmConfig:
    """Tunable alarm parameters (defaults are the T25 spec values)."""

    player_alarm: bool = True
    """Alarm when players are on screen (``WorldState.players_on_screen``)."""

    fast_hp_drop_threshold: float = 10.0
    """HP points dropped within ``fast_hp_drop_window`` that raise an alarm."""

    fast_hp_drop_window: float = 1.0
    """Time window (seconds) for the fast-HP-drop comparison (~1 second)."""

    low_hp_threshold: float = 20.0
    """HP percent strictly below which a low-HP alarm is raised."""

    pm_alarm: bool = True
    """Alarm when the PM window is open (needs a ``pm_open_check``)."""

    hp_history_size: int = 10
    """How many recent HP readings to keep for drop detection."""

    def validate(self) -> AlarmConfig:
        """Check invariants and return self (fail fast, loop-style validation).

        Raises:
            ValueError: If thresholds, the window or the history size are invalid.
        """
        if not 0.0 < self.fast_hp_drop_threshold <= 100.0:
            raise ValueError(
                "expected 0 < fast_hp_drop_threshold <= 100, "
                f"got {self.fast_hp_drop_threshold}"
            )
        if self.fast_hp_drop_window <= 0.0:
            raise ValueError(
                f"fast_hp_drop_window must be > 0, got {self.fast_hp_drop_window}"
            )
        if not 0.0 < self.low_hp_threshold <= 100.0:
            raise ValueError(
                f"expected 0 < low_hp_threshold <= 100, got {self.low_hp_threshold}"
            )
        if not isinstance(self.hp_history_size, int) or isinstance(
            self.hp_history_size, bool
        ):
            raise ValueError("hp_history_size must be an int")
        if self.hp_history_size < 1:
            raise ValueError("hp_history_size must be >= 1")
        return self


@dataclass(frozen=True)
class AlarmAction:
    """One alarm decision: what happened, how urgent, and what to do.

    Attributes:
        type: The alarm kind (``fast_hp_drop`` / ``low_hp`` /
            ``player_detected`` / ``pm_open``).
        severity: ``warning`` / ``critical``.
        message: Human-readable description (logging / UI / sound).
        recommended_action: What to do about it: ``flee`` / ``heal`` /
            ``stop`` / ``alert_user``.
    """

    type: AlarmType
    severity: Severity
    message: str
    recommended_action: RecommendedAction


class AlarmDecision:
    """Decides when to raise alarms and tracks HP history for drop detection.

    Typical usage (wired into the loop)::

        alarms = AlarmDecision(AlarmConfig(), pm_open_check=my_pm_detector)
        action = alarms.decide(state.snapshot())  # AlarmAction | None
        # the loop/UI reacts: alarm sound, flee, keep healing, alert the user.

    ``decide()`` reads a (snapshot of) :class:`WorldState` and returns the
    highest-priority alarm (or ``None`` when nothing is dangerous). It
    also appends the current HP reading to the internal history, so drop
    detection needs no external bookkeeping.

    Thread-safety contract: a single decision thread (the 20 Hz loop)
    drives this class; there is no internal locking (same as GameLoop).
    """

    def __init__(
        self,
        config: AlarmConfig | None = None,
        *,
        clock: Callable[[], float] | None = None,
        pm_open_check: Callable[[WorldState], bool] | None = None,
    ) -> None:
        """Create the alarm decision engine.

        Args:
            config: Alarm parameters; defaults to :class:`AlarmConfig`.
            clock: Monotonic-clock override (tests).
            pm_open_check: Returns True when a PM window is open. When
                ``None``, the PM alarm never fires (not detectable).

        Raises:
            ValueError: If the config is invalid.
        """
        cfg = config if config is not None else AlarmConfig()
        cfg.validate()
        self.config = cfg
        self._clock = clock if clock is not None else time.monotonic
        self._pm_open_check = pm_open_check
        self._hp_history: deque[tuple[float, float]] = deque()

    # -- decision -----------------------------------------------------------

    def decide(self, state: WorldState) -> AlarmAction | None:
        """Decide whether an alarm should fire, given the world state.

        All enabled conditions are evaluated and the highest-priority
        alarm wins (see :data:`ALARM_PRIORITY`). The current HP reading is
        appended to the history before drop detection runs.

        Returns:
            The winning :class:`AlarmAction`, or ``None`` when nothing is
            dangerous (no players, no PM, HP stable and healthy, or HP
            unknown).
        """
        self._record_hp(state)
        for alarm_type in ALARM_PRIORITY:
            action = self._detect(alarm_type, state)
            if action is not None:
                logger.info(
                    "ALARM [%s] %s: %s (recommend: %s)",
                    action.severity.value,
                    action.type.value,
                    action.message,
                    action.recommended_action.value,
                )
                return action
        return None

    # -- per-type detection --------------------------------------------------

    def _detect(self, alarm_type: AlarmType, state: WorldState) -> AlarmAction | None:
        """The alarm of one type for this state, or None when not triggered."""
        match alarm_type:
            case AlarmType.FAST_HP_DROP:
                return self._fast_hp_drop_alarm(state)
            case AlarmType.LOW_HP:
                return self._low_hp_alarm(state)
            case AlarmType.PLAYER_DETECTED:
                return self._player_alarm(state)
            case AlarmType.PM_OPEN:
                return self._pm_alarm(state)

    def _fast_hp_drop_alarm(self, state: WorldState) -> AlarmAction | None:
        """Alarm when HP fell by the threshold within the window."""
        if state.hp_percent <= 0.0:
            return None  # unknown HP: nothing to compare
        ref = self._reference_sample()
        if ref is None:
            return None
        ref_t, ref_hp = ref
        drop = ref_hp - state.hp_percent
        if drop < self.config.fast_hp_drop_threshold:
            return None
        severity, recommended = _ALARM_PROFILES[AlarmType.FAST_HP_DROP]
        return AlarmAction(
            type=AlarmType.FAST_HP_DROP,
            severity=severity,
            message=(
                f"HP dropped {drop:.1f} points in {max(0.0, self._clock() - ref_t):.2f}s "
                f"({ref_hp:.0f} -> {state.hp_percent:.0f})"
            ),
            recommended_action=recommended,
        )

    def _reference_sample(self) -> tuple[float, float] | None:
        """The ``(time, hp)`` sample the current HP is compared against.

        The newest reading at or before ``now - fast_hp_drop_window`` (i.e.
        roughly one window ago). When the history is younger than the
        window, the oldest available reading is used instead, so detection
        starts as soon as at least one earlier reading exists. ``None``
        while the history is empty.
        """
        if not self._hp_history:
            return None
        cutoff = self._clock() - self.config.fast_hp_drop_window
        for t, hp in reversed(self._hp_history):
            if t <= cutoff:
                return (t, hp)
        return self._hp_history[0]

    def _low_hp_alarm(self, state: WorldState) -> AlarmAction | None:
        """Alarm when known HP is strictly below the critical threshold."""
        hp = state.hp_percent
        if hp <= 0.0 or hp >= self.config.low_hp_threshold:
            return None
        severity, recommended = _ALARM_PROFILES[AlarmType.LOW_HP]
        return AlarmAction(
            type=AlarmType.LOW_HP,
            severity=severity,
            message=(
                f"HP {hp:.0f}% below critical threshold "
                f"{self.config.low_hp_threshold:g}%"
            ),
            recommended_action=recommended,
        )

    def _player_alarm(self, state: WorldState) -> AlarmAction | None:
        """Alarm when players are on screen (and the alarm is enabled)."""
        if not self.config.player_alarm or state.players_on_screen <= 0:
            return None
        severity, recommended = _ALARM_PROFILES[AlarmType.PLAYER_DETECTED]
        return AlarmAction(
            type=AlarmType.PLAYER_DETECTED,
            severity=severity,
            message=f"{state.players_on_screen} player(s) on screen",
            recommended_action=recommended,
        )

    def _pm_alarm(self, state: WorldState) -> AlarmAction | None:
        """Alarm when the PM window is open (and the alarm is enabled)."""
        if not self.config.pm_alarm or self._pm_open_check is None:
            return None
        if not self._pm_open_check(state):
            return None
        severity, recommended = _ALARM_PROFILES[AlarmType.PM_OPEN]
        return AlarmAction(
            type=AlarmType.PM_OPEN,
            severity=severity,
            message="PM window open",
            recommended_action=recommended,
        )

    # -- HP history ------------------------------------------------------------

    def _record_hp(self, state: WorldState) -> None:
        """Append one HP sample (unknown HP skipped) and trim the history."""
        if state.hp_percent > 0.0:
            self._hp_history.append((self._clock(), state.hp_percent))
            while len(self._hp_history) > self.config.hp_history_size:
                self._hp_history.popleft()

    @property
    def hp_history_size(self) -> int:
        """Number of HP samples currently in the history (tests/UI)."""
        return len(self._hp_history)

    def reset_history(self) -> None:
        """Clear the HP history (tests, UI reset button)."""
        self._hp_history.clear()
