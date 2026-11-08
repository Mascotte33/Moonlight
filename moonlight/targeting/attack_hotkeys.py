"""Attack hotkeys: the area rules, then the current target row's hotkey.

Area rules are checked top to bottom; the first that can fire wins. A rule
fires when its ``area`` shape covers at least ``minimum_monsters`` creature
slots and MP is at least ``minimum_mana``:

- ``around me``: the shape is centred on the player.
- ``wave``: the shape is drawn facing up; of the 4 turns the one hitting most
  wins, and the loop presses Ctrl+arrow to face it before the key.
- ``on a tile``: the shape is centred on every screen slot that is not a wall;
  most hits wins, then the slot nearest the player. The loop moves the mouse
  there before the key (client: Use at mouse cursor).
# doc: docs/prs/attack-types.md#area-kinds

Else the target row's ``hotkey`` fires, with its own mana minimum.

Every key shares one ``attack_cooldown_ms``; a new target starts it fresh, so
its first spell goes out at once (``new_target``). Mana is in points
(``mp_points``); unknown MP (0) is below any mana minimum.

The loop calls this only while the selection matches targeting's choice and
never while a lure pulls; focus gating and the rate limit are its job too.
"""

# doc: docs/prs/targeting.md#area-rules

from __future__ import annotations

import logging
from dataclasses import dataclass

from moonlight.core.state import WorldState
from moonlight.hardware.input import InputBackend
from moonlight.perception.creatures import PLAYER_SLOT
from moonlight.targeting.targeting import (
    AreaRule,
    MonsterRow,
    TargetingConfig,
    Walkable,
    screen_slots,
    wall_slots,
)

__all__ = ["WAVE_TURN_KEYS", "AttackHotkeys", "AttackPress", "count_hits"]

logger = logging.getLogger("moonlight.targeting.attack_hotkeys")

Slot = tuple[int, int]

#: Ctrl+arrow to face up, then each quarter turn clockwise.
WAVE_TURN_KEYS = ("ctrl+up", "ctrl+right", "ctrl+down", "ctrl+left")


def _turn_clockwise(area: tuple[Slot, ...], quarter_turns: int) -> tuple[Slot, ...]:
    """*area* (drawn facing up) turned clockwise; screen y grows downward."""
    for _ in range(quarter_turns):
        area = tuple((-dy, dx) for dx, dy in area)
    return area


@dataclass(frozen=True)
class AttackPress:
    """One attack: the key, plus a slot to aim the mouse at or a key to turn first."""

    hotkey: str
    aim_slot: Slot | None = None
    turn_key: str = ""


def count_hits(area: tuple[Slot, ...], center: Slot, creature_slots: tuple[Slot, ...]) -> int:
    """Creature slots the *area* offsets cover when centred on *center*."""
    covered = {(center[0] + dx, center[1] + dy) for dx, dy in area}
    return sum(1 for slot in creature_slots if slot in covered)


def _distance(a: Slot, b: Slot) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class AttackHotkeys:
    """Picks the attack key for one tick and keeps the shared cooldown."""

    def __init__(self, config: TargetingConfig, walkable: Walkable | None = None) -> None:
        config.validate()
        self.config = config
        #: The walk map: "on a tile" attacks never aim at a wall. None: no walls known.
        self._walkable = walkable
        self._last_pressed_at: float | None = None

    def _area_press(self, rule: AreaRule, state: WorldState) -> AttackPress | None:
        """*rule*'s press when its best placement hits enough creatures, else None."""
        creatures = state.creature_slots
        if rule.kind == "around me":
            if count_hits(rule.area, PLAYER_SLOT, creatures) >= rule.minimum_monsters:
                return AttackPress(rule.hotkey)
            return None
        if rule.kind == "wave":
            hits, turn_key = max(
                (count_hits(_turn_clockwise(rule.area, turns), PLAYER_SLOT, creatures), key)
                for turns, key in enumerate(WAVE_TURN_KEYS)
            )
            if hits >= rule.minimum_monsters:
                return AttackPress(rule.hotkey, turn_key=turn_key)
            return None
        walls = wall_slots(state, self._walkable)
        hits, _, slot = max(
            (
                (count_hits(rule.area, s, creatures), -_distance(s, PLAYER_SLOT), s)
                for s in screen_slots()
                if s not in walls
            ),
            default=(0, 0, PLAYER_SLOT),  # all walls: an unmapped area
        )
        if hits >= rule.minimum_monsters:
            return AttackPress(rule.hotkey, aim_slot=slot)
        return None

    def decide(self, state: WorldState, row: MonsterRow, now: float) -> AttackPress | None:
        """The attack to press now for target *row*, or ``None``."""
        if (
            self._last_pressed_at is not None
            and (now - self._last_pressed_at) * 1000 < self.config.attack_cooldown_ms
        ):
            return None
        mana = state.mp_points
        for rule in self.config.area_rules:
            if rule.enabled and mana >= rule.minimum_mana:
                press = self._area_press(rule, state)
                if press is not None:
                    return press
        if not row.hotkey or mana < row.minimum_mana:
            return None
        return AttackPress(row.hotkey)

    # doc: docs/prs/first-spell.md#fresh-cooldown
    def new_target(self) -> None:
        """A new target: its first spell need not wait for the last one's cooldown.

        The cooldown carried over from a quick kill made the next monster wait
        up to 2 s for its first spell (bot.log 2026-10-31: 1.1 s twice in 5 s).
        """
        self._last_pressed_at = None

    def send(self, hotkey: str, backend: InputBackend, now: float) -> None:
        """Press *hotkey* and start the shared cooldown."""
        backend.press_combo(hotkey)
        self._last_pressed_at = now
        logger.info("attack hotkey: %s", hotkey)
