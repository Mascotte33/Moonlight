"""Looting: every corpse with a loot ring next to the player gets looted.

Each tick the loop asks :meth:`Looting.ring_due` for a turning loot ring up
to ``reach`` tiles from the player (their own tile too). One shows the
instant its monster dies (owner 2026-11-04: no waiting after the kill); once
it has turned on its tile for ``RING_CONFIRM_SECONDS`` (a spell flash or a
rock does not) and no creature stands on it (a live amazon's outfit
sparkles the same blue, loot shots 2026-11-04) the nearest is looted, any
time, also mid-fight: right-clicked
(``loot_by_right_click``, the OTS loots on that click and walks there first
when it is far) or by pressing ``loot_hotkey`` (loots every corpse around).
Then the bot stands ``wait_ms`` so the loot lands, and the next ring is
looted, until none is left. Below ``min_hp_to_loot`` it holds still and
heals instead.
"""

# doc: docs/prs/looting.md#loot-hotkey
# doc: docs/prs/client-hotkeys.md#ring-right-click

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from moonlight.core.state import WorldState
from moonlight.hardware.clicker import ClickController
from moonlight.hardware.input import ClickSide, InputBackend
from moonlight.perception.corpses import Ring, RingWatch, slot_ring

__all__ = [
    "LootingConfig", "Looting", "RING_CONFIRM_LOOKS", "RING_CONFIRM_SECONDS", "RING_GAP",
    "RING_WALK_S_PER_TILE",
]

#: A ring is looted only once seen on its tile this many times ...
RING_CONFIRM_LOOKS = 3
#: ... over at least this many seconds: a real ring turns for seconds, a spell
#: flash or a rock caught mid-scroll shows once (loot shots 2026-11-04).
RING_CONFIRM_SECONDS = 0.4
#: Sightings older than this are forgotten.
RING_FORGET_SECONDS = 1.0
#: Least seconds between ring loots: the ring stays until the loot lands.
RING_GAP = 0.3
#: Seconds the character takes per tile walking to a far ring's corpse.
# ponytail: one fixed walk speed; read the character's speed if fast or
# slow characters re-click or wait too long.
RING_WALK_S_PER_TILE = 0.3
#: A screenshot of each looted ring and one as its loot lands, the ring circled.
# ponytail: a diagnosis aid (owner 2026-11-04: "it didnt loot"); delete it
# once ring looting is trusted.
LOOT_SHOTS_DIR = Path("logs") / "loot_rings"
#: Only the newest shots stay.
KEEP_LOOT_SHOTS = 40

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LootingConfig:
    loot_hotkey: str = "alt+q"
    wait_ms: int = 400
    min_hp_to_loot: float = 70.0
    #: Right-click the ringed corpse; off = press ``loot_hotkey`` instead.
    loot_by_right_click: bool = True


class Looting:
    """Loot each ringed corpse by a right-click or the loot hotkey, then wait."""

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
        self._game_region = game_region
        self._rings = RingWatch(game_region) if game_region is not None else None
        self._clicker = clicker
        #: The ring seen this tick, not looted yet.
        self._ring: Ring | None = None
        self._ring_clicked: Ring | None = None
        #: When each tile's ring was seen lately (its pixel -> look times).
        # ponytail: keyed by screen pixel, so a ring is confirmed only while
        # the view stands still; track map tiles if walking misses rings.
        self._ring_sightings: dict[tuple[int, int], list[float]] = {}
        #: The ring's right-click is on its way through the clicker.
        self._ring_clicking = False
        #: No ring looted before this: the last one's loot lands (and its walk ends).
        self._ring_hold_until = 0.0
        #: The bot stands still until then so the loot lands (``wait_ms``).
        self._loot_lands_at = 0.0
        #: The looted ring's pixel, shot once more when its loot lands.
        self._landed_shot_point: tuple[int, int] | None = None

    def _hp_too_low(self, snap: WorldState) -> bool:
        return 0 < snap.hp_percent < self._config.min_hp_to_loot

    def ring_due(self, reach: range, snap: WorldState) -> bool:
        """True when a loot ring shows *reach* tiles from the player and HP allows it.

        Loots are ``wait_ms`` (at least ``RING_GAP``) apart after the loot
        lands, plus the walk to a far ring, so the ring has time to go away.
        """
        self._ring = None
        now = self._clock()
        if self._frames is None or self._rings is None:
            return False
        if not (snap.client_focused and snap.screen_ok):
            return False  # no input to an unfocused client
        if self._landed_shot_point is not None and not self._ring_clicking:
            if now >= self._loot_lands_at:
                self._save_shot(self._landed_shot_point, "landed")
                self._landed_shot_point = None
        if self._ring_clicking or now < self._ring_hold_until:
            return False
        seen = self._rings.rings(self._frames(), now)
        for ring in seen:
            self._ring_sightings.setdefault(ring.point, []).append(now)
        for point, times in list(self._ring_sightings.items()):
            times[:] = [t for t in times if now - t <= RING_FORGET_SECONDS]
            if not times:
                del self._ring_sightings[point]
        assert self._game_region is not None
        creatures = {slot_ring(self._game_region, slot).point for slot in snap.creature_slots}
        rings = [
            ring for ring in seen
            if ring.tiles_away in reach and ring.point not in creatures
            and self._confirmed(ring, now)
        ]
        if rings:
            self._ring = rings[0]  # the nearest
        return self._ring is not None and not self._hp_too_low(snap)

    def _confirmed(self, ring: Ring, now: float) -> bool:
        times = self._ring_sightings[ring.point]
        return len(times) >= RING_CONFIRM_LOOKS and now - times[0] >= RING_CONFIRM_SECONDS

    def press_ring(self) -> None:
        """Loot the seen ring: right-click it (:meth:`step` drives the click) or press the hotkey.

        Raises:
            RuntimeError: When no ring is due, or right-click looting has no clicker.
        """
        if self._ring is None:
            raise RuntimeError("press_ring needs a seen ring")
        log.info("loot triggered at %s, %d tiles away", *self._ring)
        self._save_shot(self._ring.point, "ring")
        self._landed_shot_point = self._ring.point
        if self._config.loot_by_right_click:
            if self._clicker is None:
                raise RuntimeError("right-click looting needs a clicker")
            log.info("looting: right-click at %s", self._ring.point)
            self._clicker.request(self._ring.point, side=ClickSide.RIGHT)
            self._ring_clicked, self._ring_clicking = self._ring, True
        else:
            log.info("looting: press %s", self._config.loot_hotkey)
            self._input.press_combo(self._config.loot_hotkey)
            self._loot_landing(walk=0.0)
        self._ring = None

    def _save_shot(self, point: tuple[int, int], label: str) -> None:
        """Save this tick's frame with *point* circled in yellow to ``LOOT_SHOTS_DIR``."""
        assert self._frames is not None
        picture = cv2.cvtColor(np.ascontiguousarray(self._frames()), cv2.COLOR_RGB2BGR)
        cv2.circle(picture, point, 40, (0, 255, 255), 2)
        LOOT_SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        now = time.time()
        stamp = time.strftime("%H%M%S", time.localtime(now)) + f"{now % 1:.3f}"[1:]
        path = LOOT_SHOTS_DIR / f"{stamp}_{label}.png"
        if not cv2.imwrite(str(path), picture):
            raise OSError(f"could not save the loot screenshot {path}")
        log.info("looting: %s screenshot: %s", label, path)
        for old in sorted(LOOT_SHOTS_DIR.glob("*.png"))[:-KEEP_LOOT_SHOTS]:
            old.unlink()

    def _loot_landing(self, walk: float) -> None:
        wait, now = self._config.wait_ms / 1000, self._clock()
        self._loot_lands_at = now + wait + walk
        self._ring_hold_until = now + max(wait, RING_GAP) + walk

    def should_loot(self, snap: WorldState) -> bool:
        """True while a ring waits for HP, a right-click is in flight or the loot lands."""
        return (
            self._ring is not None
            or self._ring_clicking
            or self._clock() < self._loot_lands_at
        )

    def loot_first(self, snap: WorldState) -> bool:
        """True while a ring is looted and HP allows it: before the next attack.

        (bot.log 2026-11-04: the fight took the mouse and walked off right
        after the corpse click, and nothing was looted.)
        """
        return self.should_loot(snap) and not self._hp_too_low(snap)

    def step(self, snap: WorldState) -> bool:
        """Drive the right-click, then hold for ``wait_ms``; True while looting holds the bot."""
        if not (snap.client_focused and snap.screen_ok):
            return False
        if self._ring_clicking:
            assert self._clicker is not None and self._ring_clicked is not None
            if self._clicker.busy:
                self._clicker.step()
                return True
            # clicked: the character walks there, the loot lands, then walk on
            self._ring_clicking = False
            self._loot_landing(RING_WALK_S_PER_TILE * max(self._ring_clicked.tiles_away - 1, 0))
            return True
        return self.should_loot(snap)  # stand still: the loot lands, or HP comes back
