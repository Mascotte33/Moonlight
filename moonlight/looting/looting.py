"""Looting: right-click the corpse after a kill (else the loot-all hotkey).

A kill or the battle list going empty queues a loot; the bot right-clicks
the corpse's tile at once when the loop knows it; when it does not and the
fight waits up to ``RING_WAIT`` for the corpse's ring and right-clicks that
(right-click looting never presses the hotkey: the OTS ignores it). Else it
(the tile the killed creature's health bar vanished from; the OTS loots on
that click, walking there first when it is far) and ``loot_by_right_click``
is on, else presses the loot-all hotkey (Alt+Q) once after ``PRESS_DELAY``
(the corpse is not lootable the instant the monster leaves the battle
list), then holds
still for ``wait_ms`` so the loot lands and late monsters show up before it
walks on. Below ``min_hp_to_loot`` the press waits (the bot holds still and
heals) until HP is back up.

Each trigger has its own switch: ``after_kill`` (the above) and
``ringed_corpses``, which right-clicks a corpse whenever its loot ring
shows (the OTS loots it on that click, walking there first when it is
far), then stands ``wait_ms`` so the loot lands. The loop says how far a
ring may be (``reach``): up to 2 tiles from the player, their own tile too.
With only ``ringed_corpses`` on, a kill holds the bot still (no walking;
attacking goes on) for up to ``RING_WAIT`` so the ring can show and be
clicked before the cavebot walks off: the ring check is blind while the
view scrolls.
"""

# doc: docs/prs/looting.md#loot-hotkey
# doc: docs/prs/client-hotkeys.md#ring-right-click
# doc: docs/prs/client-hotkeys.md#corpse-click

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from moonlight.core.state import WorldState
from moonlight.hardware.clicker import ClickController
from moonlight.hardware.input import ClickSide, InputBackend
from moonlight.perception.corpses import Ring, RingWatch, slot_ring

__all__ = [
    "LootingConfig", "Looting", "PRESS_DELAY", "RING_GAP", "RING_WAIT", "RING_WALK_S_PER_TILE"
]

#: Seconds between the kill and the hotkey press; a press on the kill tick is
#: ignored. The corpse right-click goes at once: the health bar vanished
#: ticks before the battle list showed the kill.
PRESS_DELAY = 0.1
#: Seconds the bot stands still after a kill looking for its corpse's ring
#: (bot.log 2026-11-04: rings showed 0.2-1.3 s after the kill was seen).
RING_WAIT = 1.5
#: Least seconds between ring clicks: the ring stays until the loot lands.
RING_GAP = 0.3
#: Seconds the character takes per tile walking to a far ring's corpse.
# ponytail: one fixed walk speed; read the character's speed if fast or
# slow characters re-click or wait too long.
RING_WALK_S_PER_TILE = 0.3

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LootingConfig:
    loot_hotkey: str = "alt+q"
    wait_ms: int = 400
    min_hp_to_loot: float = 70.0
    #: Press after every kill (and when the battle list empties).
    after_kill: bool = True
    #: After a kill right-click its corpse; off = press ``loot_hotkey`` instead.
    loot_by_right_click: bool = True
    #: Right-click a corpse whenever its loot ring shows next to the player.
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
        clicker: ClickController | None = None,
    ) -> None:
        self._input = input
        self._config = config or LootingConfig()
        self._clock = clock
        self._frames = frames
        self._grid = game_region
        # right-click looting finds a kill's corpse by its ring when no tile is known
        watches_rings = self._config.ringed_corpses or self._config.loot_by_right_click
        self._rings = (
            RingWatch(game_region) if game_region is not None and watches_rings else None
        )
        self._clicker = clicker
        #: No ring click before this: the last one's loot lands (and its walk ends).
        self._ring_hold_until = 0.0
        #: A seen ring whose click is not asked for yet (the input limit dropped it).
        self._ring: Ring | None = None
        self._ring_clicked: Ring | None = None
        #: The ring's right-click is on its way through the clicker.
        self._ring_clicking = False
        self._pending: str | None = None
        #: The kill's corpse tile to right-click (None = press the loot-all hotkey).
        self._corpse: Ring | None = None
        self._pressed_at: float | None = None
        self._died_at = 0.0
        self._ring_wait_until = 0.0
        #: A kill with no known corpse tile: the fight waits for its ring until then.
        self._kill_ring_wait_until = 0.0
        #: The fight waits for a right-click's loot to land until then (``wait_ms``).
        self._loot_lands_at = 0.0

    @property
    def pending(self) -> str | None:
        """Name of the kill waiting to be looted ("" for an emptied battle list)."""
        return self._pending

    def on_target_died(
        self, target_name: str, corpse_slot: tuple[int, int] | None = None
    ) -> None:
        """Queue a loot for the corpse of *target_name* ("" = battle list emptied).

        *corpse_slot* is the game-grid ``(col, row)`` it died on, when known.
        """
        # ponytail: one queued press; a kill and the emptied list in one tick
        # share it, since the hotkey loots every corpse around.
        if not self._config.after_kill:
            if self._config.ringed_corpses:
                self._ring_wait_until = self._clock() + RING_WAIT
            return
        can_click = self._config.loot_by_right_click and self._clicker is not None
        if corpse_slot is None and can_click and self._rings is not None:
            # the OTS ignores the hotkey: wait for the ring, then right-click it
            # (bot.log 2026-11-04: no tile known, Alt+Q, then on to the next fight)
            self._kill_ring_wait_until = self._clock() + RING_WAIT
            return
        self._pending = target_name
        if corpse_slot is not None and can_click and self._grid is not None:
            self._corpse = slot_ring(self._grid, corpse_slot)

    def on_corpse_found(self, corpse_slot: tuple[int, int]) -> None:
        """The corpse tile of a kill still waiting for its ring: right-click it now.

        The battle list may drop a kill before its health bar goes, so the
        loop finds the tile a few ticks later (bot.log 2026-11-04: most kills
        waited ~1.4 s for the ring).
        """
        if self._clock() >= self._kill_ring_wait_until or self._grid is None:
            return
        log.info("looting: corpse tile found after the kill")
        self._kill_ring_wait_until = 0.0
        self._pending, self._pressed_at = "", None
        self._corpse = slot_ring(self._grid, corpse_slot)

    def should_loot(self, snap: WorldState) -> bool:
        """True when a press is queued, a ring is being clicked, or the bot waits for a ring."""
        return (
            self._pending is not None
            or self._ring_clicking
            or self._clock() < max(self._ring_wait_until, self._kill_ring_wait_until)
        )

    def loot_first(self, snap: WorldState) -> bool:
        """True while a kill or ring is looted and HP allows it: before the next attack.

        That is a queued press, a right-click in flight and its wait after
        (bot.log 2026-11-04: the fight took the mouse and walked off right
        after the corpse click, and nothing was looted), and a seen ring not
        clicked yet (13:10:28: found 3 ms before the kill's wait ran out).
        """
        looting = (
            self._pending is not None
            or self._ring is not None
            or self._ring_clicking
            or self._clock() < max(self._loot_lands_at, self._kill_ring_wait_until)
        )
        return looting and not (0 < snap.hp_percent < self._config.min_hp_to_loot)

    def ring_due(self, reach: range) -> bool:
        """True when a loot ring shows *reach* tiles from the player and a click is due.

        Clicks are ``wait_ms`` (at least ``RING_GAP``) apart after the click
        lands, plus the walk to a far ring, so the ring has time to go away and
        a ring that stays does not take every input slot. A seen ring stays due
        until :meth:`press_ring` runs.
        """
        if self._ring is not None:
            return True  # seen: clicked even when the kill's wait ran out meanwhile
        now = self._clock()
        if not (self._config.ringed_corpses or now < self._kill_ring_wait_until):
            return False  # rings only for a kill's corpse unless "Loot ringed corpses"
        if self._frames is None or self._rings is None:
            return False
        if self._ring_clicking or now < self._ring_hold_until:
            return False
        seen = self._rings.rings(self._frames(), now)
        rings = [ring for ring in seen if ring.tiles_away in reach]
        if rings:
            self._ring = rings[0]  # the nearest
            log.info("loot triggered at %s, %d tiles away", *self._ring)
        return self._ring is not None

    def press_ring(self) -> None:
        """Right-click the ringed corpse; :meth:`step` drives the click, then waits.

        Raises:
            RuntimeError: When looting has no clicker or no ring is due.
        """
        if self._clicker is None or self._ring is None:
            raise RuntimeError("press_ring needs a clicker and a seen ring")
        log.info("looting: right-click at %s", self._ring.point)
        self._clicker.request(self._ring.point, side=ClickSide.RIGHT)
        self._ring_clicked, self._ring = self._ring, None
        self._ring_clicking = True

    def step(self, snap: WorldState) -> bool:
        """Press once, then hold for ``wait_ms``; True while looting holds the bot."""
        if not (snap.client_focused and snap.screen_ok):
            return False
        if self._ring_clicking:
            assert self._clicker is not None  # press_ring checked it
            if self._clicker.busy:
                self._clicker.step()
                return True
            # clicked: the character walks there, the loot lands, then walk on
            # (this also ends a kill's ring wait early)
            assert self._ring_clicked is not None
            walk = RING_WALK_S_PER_TILE * max(self._ring_clicked.tiles_away - 1, 0)
            self._ring_clicking = False
            self._kill_ring_wait_until = 0.0  # the kill's ring is clicked
            wait, now = self._config.wait_ms / 1000, self._clock()
            self._ring_wait_until = self._loot_lands_at = now + wait + walk
            self._ring_hold_until = now + max(wait, RING_GAP) + walk
            return True
        if self._pending is None:
            # stand still, the ring check runs
            return self._clock() < max(self._ring_wait_until, self._kill_ring_wait_until)
        now = self._clock()
        if self._pressed_at is None:
            if 0 < snap.hp_percent < self._config.min_hp_to_loot:
                return True  # low HP: hold still and heal, loot after
            if self._corpse is not None:
                assert self._clicker is not None  # on_target_died checked it
                log.info("looting: right-click the corpse at %s", self._corpse.point)
                self._clicker.request(self._corpse.point, side=ClickSide.RIGHT)
                self._ring_clicked, self._ring_clicking = self._corpse, True
                self._pending = self._corpse = None
                return True
            if now - self._died_at < PRESS_DELAY:
                return True
            log.info("looting")
            self._input.press_combo(self._config.loot_hotkey)
            self._pressed_at = now
            return True
        if now - self._pressed_at < self._config.wait_ms / 1000:
            return True
        self._pending = self._pressed_at = None
        return False
