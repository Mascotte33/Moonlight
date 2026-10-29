"""HP alarms (T25): low HP and a fast HP drop.

:meth:`AlarmDecision.decide` returns the alarm message for a
:class:`moonlight.core.state.WorldState`, or None. An alarm never stops the
bot: the loop alerts, saves a screenshot and carries on (docs/prs/extras.md#alarms).
Player and private message alarms belong to Extras.

Alarm conditions (the first hit wins):

* **fast HP drop** (``fast_hp_drop_alarm``): HP fell by
  ``fast_hp_drop_threshold`` points within roughly ``fast_hp_drop_window``
  seconds, by the last ``hp_history_size`` readings.
* **low HP** (``low_hp_alarm``): known HP is strictly below
  ``low_hp_threshold`` percent.

The messages hold no live numbers, so one lasting alarm keeps one message.

``hp_percent`` of 0.0 means "unknown" (never read): no alarm and the reading
is left out of the HP history. ``client_focused`` / ``screen_ok`` gating is
the loop's job. The clock is injectable for tests. A single decision thread
(the loop) drives this class; there is no locking.

# doc: docs/prs/extras.md#alarms
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from moonlight.core.state import WorldState

__all__ = ["AlarmConfig", "AlarmDecision"]


@dataclass(frozen=True)
class AlarmConfig:
    """Tunable alarm parameters (the ``alarms`` section of the config)."""

    low_hp_alarm: bool = True
    """Alarm when HP is below ``low_hp_threshold``."""

    low_hp_threshold: float = 20.0
    """HP percent strictly below which a low-HP alarm is raised."""

    fast_hp_drop_alarm: bool = True
    """Alarm when HP drops fast."""

    fast_hp_drop_threshold: float = 10.0
    """HP points dropped within ``fast_hp_drop_window`` that raise an alarm."""

    fast_hp_drop_window: float = 1.0
    """Time window (seconds) for the fast-HP-drop comparison."""

    hp_history_size: int = 10
    """How many recent HP readings to keep for drop detection."""

    def validate(self) -> AlarmConfig:
        """Check invariants and return self (fail fast).

        Raises:
            ValueError: If thresholds, the window or the history size are invalid.
        """
        if not 0.0 < self.fast_hp_drop_threshold <= 100.0:
            raise ValueError(
                f"expected 0 < fast_hp_drop_threshold <= 100, got {self.fast_hp_drop_threshold}"
            )
        if self.fast_hp_drop_window <= 0.0:
            raise ValueError(f"fast_hp_drop_window must be > 0, got {self.fast_hp_drop_window}")
        if not 0.0 < self.low_hp_threshold <= 100.0:
            raise ValueError(f"expected 0 < low_hp_threshold <= 100, got {self.low_hp_threshold}")
        if not isinstance(self.hp_history_size, int) or isinstance(self.hp_history_size, bool):
            raise ValueError("hp_history_size must be an int")
        if self.hp_history_size < 1:
            raise ValueError("hp_history_size must be >= 1")
        return self


class AlarmDecision:
    """Decides when an HP alarm is on and keeps the HP history for drop detection."""

    def __init__(
        self, config: AlarmConfig | None = None, *, clock: Callable[[], float] | None = None
    ) -> None:
        """Create the engine.

        Raises:
            ValueError: If the config is invalid.
        """
        self.config = (config if config is not None else AlarmConfig()).validate()
        self._clock = clock if clock is not None else time.monotonic
        self._hp_history: deque[tuple[float, float]] = deque()

    def decide(self, state: WorldState) -> str | None:
        """The alarm message for this state, or None when HP is fine or unknown.

        Call it once per tick: it appends the current HP to the history first.
        """
        hp = state.hp_percent
        if hp <= 0.0:
            return None
        cfg = self.config
        reference = self._reference_hp()
        self._hp_history.append((self._clock(), hp))
        while len(self._hp_history) > cfg.hp_history_size:
            self._hp_history.popleft()
        if cfg.fast_hp_drop_alarm and reference is not None:
            if reference - hp >= cfg.fast_hp_drop_threshold:
                return (
                    f"HP dropped {cfg.fast_hp_drop_threshold:g}+ points "
                    f"in {cfg.fast_hp_drop_window:g} s"
                )
        if cfg.low_hp_alarm and hp < cfg.low_hp_threshold:
            return f"HP below {cfg.low_hp_threshold:g}%"
        return None

    def _reference_hp(self) -> float | None:
        """The HP the current reading is compared against.

        The newest reading at or before ``now - fast_hp_drop_window``. While
        the history is younger than the window, the oldest reading is used,
        so detection starts as soon as one earlier reading exists. None while
        the history is empty.
        """
        if not self._hp_history:
            return None
        cutoff = self._clock() - self.config.fast_hp_drop_window
        for t, hp in reversed(self._hp_history):
            if t <= cutoff:
                return hp
        return self._hp_history[0][1]
