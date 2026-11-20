"""Looting: the loot hotkey loots every ringed corpse around the player.

Each tick the loop asks :meth:`Looting.ring_due` for a turning loot ring on
the player's tile or the 8 around it. One shows the instant its monster
dies (owner 2026-11-04: no waiting after the kill); once it has turned on
its tile for ``RING_CONFIRM_SECONDS`` (a spell flash or a rock does not)
and no creature stands on it (a live amazon's outfit sparkles the same
blue, loot shots 2026-11-04), ``loot_hotkey`` is pressed, any time, also
mid-fight. The client's quick loot takes every corpse in that 3x3 at once.
Then the bot stands ``wait_ms`` and until no ring shows around it for
``RING_GONE_SECONDS`` (owner 2026-11-04: the rings go away once looted,
then the next target). Rings still there ``RING_GONE_TIMEOUT`` after a
press get one more press; then they are left (a full backpack) until the
character moves. With no fight on, a ring 2 tiles away is walked to, a
step at a time. Below ``min_hp_to_loot`` it holds still and heals instead.
"""

# doc: docs/prs/looting.md#loot-hotkey
# doc: docs/prs/client-hotkeys.md#hotkey-loot

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from moonlight.cavebot.cavebot import WALK_KEYS
from moonlight.core.random_delay import random_delay
from moonlight.core.state import Position, WorldState
from moonlight.hardware.input import InputBackend
from moonlight.perception.corpses import Ring, RingWatch, slot_ring
from moonlight.perception.creatures import PLAYER_SLOT

__all__ = [
    "LootingConfig", "Looting", "RING_CONFIRM_LOOKS", "RING_CONFIRM_SECONDS",
    "RING_GONE_SECONDS", "RING_GONE_TIMEOUT", "RING_STEP_SECONDS",
]

#: A ring is looted only once seen this many times, up to a tile off ...
RING_CONFIRM_LOOKS = 3
#: ... over at least this many seconds: a real ring turns for seconds, a spell
#: flash or a rock caught mid-scroll shows once (loot shots 2026-11-04).
RING_CONFIRM_SECONDS = 0.4
#: Sightings older than this are forgotten.
RING_FORGET_SECONDS = 1.0
#: The rings around the player are gone once none was seen this long (a
#: ring's dots hide up to about 0.6 s of each turn) ...
RING_GONE_SECONDS = 0.8
#: ... and the hotkey is pressed again when one still shows this long after a press.
RING_GONE_TIMEOUT = 1.5
#: Presses for the same rings before they are left until the character moves.
RING_PRESSES = 2
#: Seconds between two steps toward a ring 2 tiles away (one step takes about 0.3 s).
RING_STEP_SECONDS = 0.5
#: Steps toward it before a ring 2 tiles away is left (a wall or a player in the way).
RING_STEPS = 3
#: A screenshot of each loot press, the ring circled.
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


class Looting:
    """Press the loot hotkey for the ringed corpses around the player, then wait."""

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
        self._game_region = game_region
        self._rings = RingWatch(game_region) if game_region is not None else None
        #: The ring around the player seen this tick, not looted yet.
        self._ring: Ring | None = None
        #: A ring 2 tiles away seen this tick: walked to when no fight is on.
        self._far_ring: Ring | None = None
        #: Where and when rings were seen lately.
        self._ring_sightings: list[tuple[tuple[int, int], float]] = []
        #: When the hotkey was last pressed, and how often for these rings.
        self._pressed_at: float | None = None
        #: ``wait_ms`` with a random spread, picked at each press.
        self._wait_seconds = 0.0
        self._presses = 0
        #: Rings around this position were pressed for and stayed: left alone here.
        self._given_up_at: Position | None = None
        self._next_step_at = float("-inf")
        #: Steps toward a ring 2 tiles away since the last press.
        self._steps = 0

    def _hp_too_low(self, snap: WorldState) -> bool:
        return 0 < snap.hp_percent < self._config.min_hp_to_loot

    def ring_due(self, reach: range, snap: WorldState) -> bool:
        """True when the hotkey should be pressed for a ring *reach* tiles away.

        *reach* is the tiles looted by the hotkey, ``range(0, 2)``. A ring
        one tile farther is kept for :meth:`step` to walk to.
        """
        self._ring = self._far_ring = None
        now = self._clock()
        if self._frames is None or self._rings is None:
            return False
        if not (snap.client_focused and snap.screen_ok):
            return False  # no input to an unfocused client
        if self._given_up_at is not None and snap.position != self._given_up_at:
            self._given_up_at = None  # moved: the rings here are new ones
        seen = self._rings.rings(self._frames(), now)
        self._ring_sightings = [
            (point, t) for point, t in self._ring_sightings if now - t <= RING_FORGET_SECONDS
        ] + [(ring.point, now) for ring in seen]
        assert self._game_region is not None
        creatures = {slot_ring(self._game_region, slot).point for slot in snap.creature_slots}
        rings = [
            ring for ring in seen
            if ring.point not in creatures and self._confirmed(ring, now)
        ]
        if self._holding(now, snap.position) or self._given_up_at is not None:
            return False
        self._far_ring = next((r for r in rings if r.tiles_away == reach.stop), None)
        if self._far_ring is None:
            self._steps = 0
        self._ring = next((r for r in rings if r.tiles_away in reach), None)  # the nearest
        return self._ring is not None and not self._hp_too_low(snap)

    def _near(self, a: tuple[int, int], b: tuple[int, int]) -> bool:
        """Up to a tile apart (the view scrolls as one walks)."""
        assert self._game_region is not None
        tile = self._game_region[2] / 15  # the grid is 15 tiles wide
        return abs(a[0] - b[0]) <= tile and abs(a[1] - b[1]) <= tile

    def _confirmed(self, ring: Ring, now: float) -> bool:
        """Seen enough looks for long enough, up to a tile off."""
        times = [t for point, t in self._ring_sightings if self._near(point, ring.point)]
        return len(times) >= RING_CONFIRM_LOOKS and now - times[0] >= RING_CONFIRM_SECONDS

    def _rings_around_gone(self, now: float) -> bool:
        """No ring seen on the player's tile or the 8 around it for ``RING_GONE_SECONDS``."""
        assert self._game_region is not None
        player = slot_ring(self._game_region, PLAYER_SLOT).point
        return not any(
            self._near(point, player) and now - t < RING_GONE_SECONDS
            for point, t in self._ring_sightings
        )

    def _holding(self, now: float, position: Position) -> bool:
        """True while a press's loot lands and its rings go; ends the hold when they do or stay."""
        if self._pressed_at is None:
            return False
        if now < self._pressed_at + self._wait_seconds:
            return True
        if self._rings_around_gone(now):
            log.info("looting: rings around the player gone")
            self._pressed_at, self._presses = None, 0
            return False
        if now - self._pressed_at < RING_GONE_TIMEOUT:
            return True
        self._pressed_at = None  # still there: press again, or give up below
        if self._presses >= RING_PRESSES:
            log.warning("looting: rings still there after %d presses, left here", self._presses)
            self._given_up_at, self._presses = position, 0
        else:
            log.warning("looting: rings still there, pressing again")
        return False

    def press_ring(self) -> None:
        """Press the loot hotkey for the seen ring.

        Raises:
            RuntimeError: When no ring is due.
        """
        if self._ring is None:
            raise RuntimeError("press_ring needs a seen ring")
        log.info("looting: ring at %s, %d tiles away: press %s", *self._ring,
                 self._config.loot_hotkey)
        self._save_shot(self._ring.point)
        self._input.press_combo(self._config.loot_hotkey)
        self._pressed_at, self._presses, self._steps = self._clock(), self._presses + 1, 0
        # doc: docs/prs/random-delays.md#spread
        self._wait_seconds = random_delay(self._config.wait_ms / 1000)
        self._ring = None

    def _save_shot(self, point: tuple[int, int]) -> None:
        """Save this tick's frame with *point* circled in yellow to ``LOOT_SHOTS_DIR``."""
        assert self._frames is not None
        picture = cv2.cvtColor(np.ascontiguousarray(self._frames()), cv2.COLOR_RGB2BGR)
        cv2.circle(picture, point, 40, (0, 255, 255), 2)
        LOOT_SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        now = time.time()
        stamp = time.strftime("%H%M%S", time.localtime(now)) + f"{now % 1:.3f}"[1:]
        path = LOOT_SHOTS_DIR / f"{stamp}_ring.png"
        if not cv2.imwrite(str(path), picture):
            raise OSError(f"could not save the loot screenshot {path}")
        log.info("looting: screenshot: %s", path)
        for old in sorted(LOOT_SHOTS_DIR.glob("*.png"))[:-KEEP_LOOT_SHOTS]:
            old.unlink()

    def should_loot(self, snap: WorldState) -> bool:
        """True while a ring waits for HP, a press's loot lands, or a ring 2 tiles away waits."""
        return self.loot_first(snap) or self._ring is not None or self._far_ring is not None

    def loot_first(self, snap: WorldState) -> bool:
        """True while a press's rings go and HP allows it: before the next attack.

        (bot.log 2026-11-04: the fight walked off right after the loot, and
        nothing was looted.)
        """
        busy = self._ring is not None or self._holding(self._clock(), snap.position)
        return busy and not self._hp_too_low(snap)

    def step(self, snap: WorldState) -> bool:
        """Stand still while the loot lands, else step toward a ring 2 tiles away."""
        if not (snap.client_focused and snap.screen_ok):
            return False
        now = self._clock()
        if self._ring is None and not self._holding(now, snap.position) and self._far_ring:
            if self._steps >= RING_STEPS:
                log.warning("looting: ring at %s not reached, left here", self._far_ring.point)
                self._given_up_at, self._far_ring, self._steps = snap.position, None, 0
                return False
            if now >= self._next_step_at:
                assert self._game_region is not None
                (rx, ry), (px, py) = self._far_ring.point, slot_ring(
                    self._game_region, PLAYER_SLOT
                ).point
                key = WALK_KEYS[((rx > px) - (rx < px), (ry > py) - (ry < py))]
                log.info("looting: ring at %s 2 tiles away: step %s", self._far_ring.point, key)
                self._input.press_combo(key)
                self._next_step_at = now + random_delay(RING_STEP_SECONDS)
                self._steps += 1
            return True
        return self.should_loot(snap)  # stand still: the loot lands, or HP comes back
