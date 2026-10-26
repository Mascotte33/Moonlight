"""Attack hotkeys: the area rules, then the current target row's hotkey.

Area rules are checked top to bottom; the first one with at least
``minimum_adjacent_monsters`` creature slots touching the player's slot
(the 8 squares around it) and MP at least ``minimum_mana_percent`` fires.
Else the target row's ``hotkey`` fires, with its own mana minimum. Every key
shares one ``attack_cooldown_ms``. Unknown MP (0.0) is below any mana minimum,
so it blocks every rule that has one.

The loop calls this only while the selection matches targeting's choice and
never while a lure pulls; focus gating and the rate limit are its job too.
"""

# doc: docs/prs/targeting.md#area-rules

from __future__ import annotations

import logging

from moonlight.core.state import WorldState
from moonlight.hardware.input import InputBackend
from moonlight.perception.creatures import PLAYER_SLOT
from moonlight.targeting.targeting import MonsterRow, TargetingConfig

__all__ = ["AttackHotkeys", "count_adjacent_creatures"]

logger = logging.getLogger("moonlight.targeting.attack_hotkeys")


def count_adjacent_creatures(creature_slots: tuple[tuple[int, int], ...]) -> int:
    """Creature slots within one column and one row of the player's slot.

    ``find_creatures`` already leaves the player's own slot out.
    """
    player_column, player_row = PLAYER_SLOT
    return sum(
        1
        for column, row in creature_slots
        if abs(column - player_column) <= 1 and abs(row - player_row) <= 1
    )


class AttackHotkeys:
    """Picks the attack key for one tick and keeps the shared cooldown."""

    def __init__(self, config: TargetingConfig) -> None:
        config.validate()
        self.config = config
        self._last_pressed_at: float | None = None

    def decide(self, state: WorldState, row: MonsterRow, now: float) -> str | None:
        """The hotkey to press now for target *row*, or ``None``."""
        if (
            self._last_pressed_at is not None
            and (now - self._last_pressed_at) * 1000 < self.config.attack_cooldown_ms
        ):
            return None
        mana = state.mp_percent
        adjacent_creatures = count_adjacent_creatures(state.creature_slots)
        for rule in self.config.area_rules:
            if (
                adjacent_creatures >= rule.minimum_adjacent_monsters
                and mana >= rule.minimum_mana_percent
            ):
                return rule.hotkey
        if row.hotkey and mana >= row.minimum_mana_percent:
            return row.hotkey
        return None

    def send(self, hotkey: str, backend: InputBackend, now: float) -> None:
        """Press *hotkey* and start the shared cooldown."""
        backend.press_combo(hotkey)
        self._last_pressed_at = now
        logger.info("attack hotkey: %s", hotkey)
