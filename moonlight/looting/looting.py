"""Looting: one press of the client's loot-all hotkey after a kill.

The OTS loots every nearby corpse on one hotkey (Alt+Q). A kill or the
battle list going empty queues a press; the bot holds still ``PRESS_DELAY``
(the corpse is not lootable the instant the monster leaves the battle list),
presses it once, then holds
still for ``wait_ms`` so the loot lands and late monsters show up before it
walks on. Below ``min_hp_to_loot`` the press waits (the bot holds still and
heals) until HP is back up.

Each trigger has its own switch: ``after_kill`` (the above) and
``ringed_corpses``, which presses the hotkey whenever a corpse's loot ring
shows on the player's tile or the 8 around it, without holding the bot.
With only ``ringed_corpses`` on, a kill holds the bot still (no walking;
attacking goes on) for up to ``RING_WAIT`` so the ring can show and be
pressed before the cavebot walks off: the ring check is blind while the
view scrolls.
"""

# doc: docs/prs/looting.md#loot-hotkey

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from moonlight.core.state import WorldState
from moonlight.hardware.input import InputBackend
from moonlight.perception.corpses import RingWatch

__all__ = ["LootingConfig", "Looting", "PRESS_DELAY", "RING_GAP", "RING_WAIT"]

#: Seconds between the kill and the press; a press on the kill tick is ignored.
PRESS_DELAY = 0.3
#: Seconds the bot stands still after a kill looking for its corpse's ring.
RING_WAIT = 1.0
#: Least seconds between ring presses: the ring stays until the loot lands.
RING_GAP = 0.5

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LootingConfig:
    loot_hotkey: str = "alt+q"
    wait_ms: int = 400
    min_hp_to_loot: float = 70.0
    #: Press after every kill (and when the battle list empties).
    after_kill: bool = True
    #: Press whenever a loot ring shows next to the player.
    ringed_corpses: bool = False


class Looting:
    """Press the loot-all hotkey after a kill, then wait before walking on."""

    def __init__(
        self,
        input: InputBackend,
        config: LootingConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        frames: Callable[[], np.ndarray] | None = None,
        game_region: tuple[int, int, int, int] | None = None,
    ) -> None:
        self._input = input
        self._config = config or LootingConfig()
        self._clock = clock
        self._frames = frames
        self._rings = RingWatch(game_region) if game_region is not None else None
        self._ring_pressed_at: float | None = None
        self._pending: str | None = None
        self._pressed_at: float | None = None
        self._died_at = 0.0
        self._ring_wait_until = 0.0

    @property
    def pending(self) -> str | None:
        """Name of the kill waiting to be looted ("" for an emptied battle list)."""
        return self._pending

    def on_target_died(self, target_name: str) -> None:
        """Queue a press for the corpse of *target_name* ("" = battle list emptied)."""
        # ponytail: one queued press; a kill and the emptied list in one tick
        # share it, since the hotkey loots every corpse around.
        if not self._config.after_kill:
            if self._config.ringed_corpses:
                self._ring_wait_until = self._clock() + RING_WAIT
            return
        self._pending = target_name
        self._pressed_at = None
        self._died_at = self._clock()

    def should_loot(self, snap: WorldState) -> bool:
        """True when a press is queued or the bot waits for a kill's ring."""
        return self._pending is not None or self._clock() < self._ring_wait_until

    def loot_first(self, snap: WorldState) -> bool:
        """True when a press is queued and HP allows it: loot before the next attack."""
        return self._pending is not None and not (
            0 < snap.hp_percent < self._config.min_hp_to_loot
        )

    def ring_due(self) -> bool:
        """True when a loot ring shows next to the player and a ring press is due.

        Presses are ``wait_ms`` (at least ``RING_GAP``) apart, so the ring has
        time to go away and a ring that stays does not take every input slot.
        """
        if not self._config.ringed_corpses or self._frames is None or self._rings is None:
            return False
        now, last = self._clock(), self._ring_pressed_at
        if last is not None and now - last < max(self._config.wait_ms / 1000, RING_GAP):
            return False
        return self._rings.seen(self._frames(), now)

    def press_ring(self) -> None:
        """Press the loot hotkey for a ring, then stand ``wait_ms`` so the loot lands."""
        log.info("loot trigger: ring")
        self._input.press_combo(self._config.loot_hotkey)
        now = self._ring_pressed_at = self._clock()
        # also ends a kill's ring wait early: the loot lands, then walk
        self._ring_wait_until = now + self._config.wait_ms / 1000

    def step(self, snap: WorldState) -> bool:
        """Press once, then hold for ``wait_ms``; True while looting holds the bot."""
        if not (snap.client_focused and snap.screen_ok):
            return False
        if self._pending is None:
            return self._clock() < self._ring_wait_until  # stand still, the ring check runs
        now = self._clock()
        if self._pressed_at is None:
            if now - self._died_at < PRESS_DELAY:
                return True
            if 0 < snap.hp_percent < self._config.min_hp_to_loot:
                return True  # low HP: hold still and heal, loot after
            log.info("loot trigger: kill (%s)", self._pending or "last monster")
            self._input.press_combo(self._config.loot_hotkey)
            self._pressed_at = now
            return True
        if now - self._pressed_at < self._config.wait_ms / 1000:
            return True
        self._pending = self._pressed_at = None
        return False
