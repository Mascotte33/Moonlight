"""Looting decisions (T23).

First pass, one action per tick:

1. ``on_target_died(name)`` marks a corpse as pending.
2. ``step``: no loot window visible -> press ``loot_hotkey`` to open it
   (gives up after :data:`OPEN_TIMEOUT_TICKS` ticks without a window).
3. Window open -> move the items: ``move_all_hotkey`` if configured,
   otherwise nothing yet (per-item drag-and-drop arrives with T24).
4. Next tick -> press ``loot_hotkey`` again to close the window; done.

Capacity: ``WorldState.capacity.percent`` is the *used* share of the cap
bar, so free cap is ``100 - percent``. Looting is skipped when free cap is
below ``min_cap_to_loot``; an unknown cap (``max_weight == 0``) does not
block looting (the client itself refuses items that do not fit).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from moonlight.core.state import WorldState
from moonlight.hardware.clicker import ClickController
from moonlight.hardware.input import InputBackend
from moonlight.perception.loot import LootReader

__all__ = ["OPEN_TIMEOUT_TICKS", "LootingConfig", "Looting"]

#: Ticks to wait for the loot window after pressing the hotkey (0.5 s @ 20 Hz).
OPEN_TIMEOUT_TICKS = 10


@dataclass(frozen=True)
class LootingConfig:
    loot_hotkey: str = "F7"
    move_all_hotkey: str | None = None
    max_items_per_loot: int = 25
    min_cap_to_loot: float = 10.0


class Looting:
    """Open the last kill's corpse, move its items, close the window."""

    def __init__(
        self,
        loot_reader: LootReader,
        clicker: ClickController,
        input: InputBackend,
        state: WorldState,
        config: LootingConfig | None = None,
        *,
        frame_provider: Callable[[], np.ndarray],
    ) -> None:
        self._reader = loot_reader
        self._clicker = clicker  # per-item drags (T24)
        self._input = input
        self._state = state
        self._config = config or LootingConfig()
        self._frame_provider = frame_provider
        self._pending: str | None = None
        self._wait_ticks = 0
        self._moved = False

    @property
    def pending(self) -> str | None:
        """Name of the kill waiting to be looted, if any."""
        return self._pending

    def on_target_died(self, target_name: str) -> None:
        """Queue the corpse of *target_name* for looting."""
        # ponytail: one pending corpse, the latest kill wins; queue kills if multi-mob pulls matter
        self._pending = target_name
        self._wait_ticks = 0
        self._moved = False

    def should_loot(self, snap: WorldState) -> bool:
        """True when a kill is pending and free cap is above the minimum."""
        if self._pending is None:
            return False
        cap = snap.capacity
        return cap.max_weight <= 0 or 100.0 - cap.percent >= self._config.min_cap_to_loot

    def step(self, snap: WorldState) -> bool:
        """Run one looting tick; True while looting is still in progress."""
        if self._pending is None or not (snap.client_focused and snap.screen_ok):
            return False
        window = self._reader.read_loot_window(self._frame_provider())
        if window is None:
            if self._moved or self._wait_ticks >= OPEN_TIMEOUT_TICKS:
                return self._finish()  # closed, or the corpse never opened
            if self._wait_ticks == 0:
                self._input.press_combo(self._config.loot_hotkey)
            self._wait_ticks += 1
            return True
        if not self._moved:
            if self._config.move_all_hotkey and window.items:
                self._input.press_combo(self._config.move_all_hotkey)
            self._moved = True
            return True
        self._input.press_combo(self._config.loot_hotkey)  # close
        return self._finish()

    def _finish(self) -> bool:
        self._pending = None
        self._wait_ticks = 0
        self._moved = False
        return False
