"""Looting: one press of the client's loot-all hotkey after a kill.

The OTS loots every nearby corpse on one hotkey (Alt+Q). A kill or the
battle list going empty queues a press; the bot presses it once, then holds
still for ``wait_ms`` so the loot lands and late monsters show up before it
walks on. Skipped (not delayed) while HP is below ``min_hp_to_loot``, and
when free cap is below ``min_cap_to_loot``.

Capacity: ``WorldState.capacity.percent`` is the *used* share of the cap
bar, so free cap is ``100 - percent``; an unknown cap (``max_weight == 0``)
does not block looting.
"""

# doc: docs/prs/looting.md#loot-hotkey

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from moonlight.core.state import WorldState
from moonlight.hardware.input import InputBackend

__all__ = ["LootingConfig", "Looting"]


@dataclass(frozen=True)
class LootingConfig:
    loot_hotkey: str = "alt+q"
    wait_ms: int = 400
    min_hp_to_loot: float = 30.0
    min_cap_to_loot: float = 10.0


class Looting:
    """Press the loot-all hotkey after a kill, then wait before walking on."""

    def __init__(
        self,
        input: InputBackend,
        config: LootingConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._input = input
        self._config = config or LootingConfig()
        self._clock = clock
        self._pending: str | None = None
        self._pressed_at: float | None = None

    @property
    def pending(self) -> str | None:
        """Name of the kill waiting to be looted ("" for an emptied battle list)."""
        return self._pending

    def on_target_died(self, target_name: str) -> None:
        """Queue a press for the corpse of *target_name* ("" = battle list emptied)."""
        # ponytail: one queued press; a kill and the emptied list in one tick
        # share it, since the hotkey loots every corpse around.
        self._pending = target_name
        self._pressed_at = None

    def should_loot(self, snap: WorldState) -> bool:
        """True when a press is queued and free cap is above the minimum."""
        if self._pending is None:
            return False
        cap = snap.capacity
        return cap.max_weight <= 0 or 100.0 - cap.percent >= self._config.min_cap_to_loot

    def step(self, snap: WorldState) -> bool:
        """Press once, then hold for ``wait_ms``; True while looting holds the bot."""
        if self._pending is None or not (snap.client_focused and snap.screen_ok):
            return False
        now = self._clock()
        if self._pressed_at is None:
            if 0 < snap.hp_percent < self._config.min_hp_to_loot:
                self._pending = None  # low HP: skip, healing comes first
                return False
            self._input.press_combo(self._config.loot_hotkey)
            self._pressed_at = now
            return True
        if now - self._pressed_at < self._config.wait_ms / 1000:
            return True
        self._pending = self._pressed_at = None
        return False
