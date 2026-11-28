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
character moves. With no fight on, a ring 2 to ``RING_WALK_TILES`` tiles away is
walked to, a step at a time (ranged kills leave corpses 3-5 tiles off: one was
left behind 4 tiles away, 2026-11-28). Below ``min_hp_to_loot`` it holds still and heals instead.
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

from moonlight.cavebot.route import WALK_KEYS
from moonlight.core.random_delay import random_delay, random_reaction
from moonlight.core.state import Position, WorldState
from moonlight.hardware.input import InputBackend
from moonlight.perception.corpses import Ring, RingWatch, slot_ring
from moonlight.perception.creatures import PLAYER_SLOT

__all__ = [
    "LootingConfig", "Looting", "RING_CONFIRM_LOOKS", "RING_CONFIRM_SECONDS",
    "RING_GONE_SECONDS", "RING_GONE_TIMEOUT", "RING_STEP_SECONDS", "RING_WALK_TILES",
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
RING_PRESSES = 3
#: Farthest ring walked to (tiles); the hotkey loots up to 1 tile away.
RING_WALK_TILES = 5
#: Seconds between two steps toward a ring 2+ tiles away (one step takes about 0.3 s).
RING_STEP_SECONDS = 0.5
#: Steps beyond its distance before a ring is left (a wall or a player in the way):
#: 3 for a ring 2 tiles away, as before.
RING_EXTRA_STEPS = 2
#: A selected target that leaves the battle list was killed (or walked off: then the
#: wait below only costs a moment). Any HP: one hit from 40 % killed trolls the 25 %
#: floor missed, and the cavebot walked off the corpse (2026-11-21) ...
KILL_HP = 100.0
#: ... and its corpse's ring shows a moment later: no new attack before this many
#: seconds (random) pass or a ring by the player is seen (owner 2026-11-21: the bot
#: attacked the next monster before the ring showed and walked off the corpse).
KILL_RING_WAIT = 0.8
#: Seconds (random) after a kill's first loot press to press again: the target can
#: leave the battle list a moment before its corpse lies there to loot.
SECOND_PRESS_SECONDS = 0.6
#: Presses for a kill next to the player (the first, then one every
#: ``SECOND_PRESS_SECONDS``): a corpse that lands late is still looted.
KILL_PRESSES = 2
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
    # doc: docs/prs/drop-items.md#drop-items
    #: Every 1-3 min, drop the picked items (``DROP_ITEMS_DIR``) out of the backpacks.
    drop_items: bool = False


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
        #: A ring by the player was seen lately and is not confirmed yet.
        self._checking = False
        #: The selected target's last HP, and until when a kill waits for its ring.
        self._target_hp: float | None = None
        #: A creature stood next to the player while the target was selected; a kill
        #: then presses the loot hotkey at once, no ring needed (owner 2026-11-21).
        self._target_near = False
        self._kill_press = False
        #: When a kill's second loot press goes: the first can come before the corpse.
        self._second_press_at: float | None = None
        self._kill_presses = 0
        self._kill_wait_until = float("-inf")
        #: The nearest ring 2+ tiles away seen this tick: walked to when no fight is on.
        self._far_ring: Ring | None = None
        #: Where and when rings were seen lately.
        self._ring_sightings: list[tuple[tuple[int, int], float]] = []
        #: When the hotkey was last pressed, and how often for these rings.
        self._pressed_at: float | None = None
        #: ``wait_ms`` with a random spread, picked at each press.
        self._wait_seconds = 0.0
        # doc: docs/prs/human-timing.md#reaction
        #: ``RING_CONFIRM_SECONDS`` up to half longer, picked again at each press.
        self._ring_confirm_seconds = random_reaction(RING_CONFIRM_SECONDS)
        self._presses = 0
        #: Rings around this position were pressed for and stayed: left alone here.
        self._given_up_at: Position | None = None
        self._next_step_at = float("-inf")
        #: Steps toward a far ring since the last press, and the steps it may take.
        self._steps = 0
        self._step_budget = 0
        #: The last far ring seen, when, and where the character stood.
        self._last_far: tuple[Ring, float, Position] | None = None

    def _hp_too_low(self, snap: WorldState) -> bool:
        return 0 < snap.hp_percent < self._config.min_hp_to_loot

    def ring_due(self, reach: range, snap: WorldState) -> bool:
        """True when the hotkey should be pressed for a ring *reach* tiles away.

        *reach* is the tiles looted by the hotkey, ``range(0, 2)``. A ring
        one tile farther is kept for :meth:`step` to walk to.
        """
        self._ring = self._far_ring = None
        self._checking = False
        now = self._clock()
        if self._second_press_at is not None and now >= self._second_press_at:
            self._second_press_at = None
            self._kill_press = True  # pressed below like the first
            self._pressed_at = None
        if self._kill_press and self._pressed_at is None:
            # A kill next to the player: Quick Loot takes every corpse around, no ring needed.
            if self._frames is not None and snap.client_focused and snap.screen_ok:
                assert self._game_region is not None
                self._ring = Ring(slot_ring(self._game_region, PLAYER_SLOT).point, 0)
                return not self._hp_too_low(snap)
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
        # A ring by the player still being confirmed: no new target walks off from it.
        player = slot_ring(self._game_region, PLAYER_SLOT).point
        self._checking = any(
            self._near(point, player) and point not in creatures
            for point, _ in self._ring_sightings
        )
        walk = range(reach.stop, RING_WALK_TILES + 1)
        self._far_ring = next((r for r in rings if r.tiles_away in walk), None)  # the nearest
        if self._far_ring is not None:
            self._last_far = (self._far_ring, now, snap.position)
        elif self._last_far is not None:
            # A ring's dots hide up to 0.6 s of each turn: keep it that long while the
            # character stands, or the route walked off from it (2026-11-28).
            ring, seen_at, at = self._last_far
            if now - seen_at < RING_GONE_SECONDS and snap.position == at:
                self._far_ring = ring
            else:
                self._last_far = None
        if self._far_ring is None:
            self._steps = 0
        elif self._steps == 0:
            self._step_budget = self._far_ring.tiles_away - 1 + RING_EXTRA_STEPS
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
        return len(times) >= RING_CONFIRM_LOOKS and now - times[0] >= self._ring_confirm_seconds

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
        dropped = getattr(self._input, "dropped", 0)
        self._input.press_combo(self._config.loot_hotkey)
        if getattr(self._input, "dropped", 0) != dropped:
            # The wire rate limit kept it (right after attack or heal keys): the ring
            # stays due and the press goes again next tick, not "looted".
            log.info("looting: the press was held back by the rate limit, again next tick")
            return
        if self._kill_press:
            self._kill_presses += 1
            if self._kill_presses < KILL_PRESSES:
                self._second_press_at = self._clock() + random_delay(SECOND_PRESS_SECONDS)
            else:
                self._kill_presses = 0
        # Once: the second press scheduled another one each time (Alt+Q every 0.6 s
        # for 1.5 min, no attack, 2026-11-21).
        self._kill_press = False
        self._save_shot(self._ring.point)
        self._pressed_at, self._presses, self._steps = self._clock(), self._presses + 1, 0
        # doc: docs/prs/random-delays.md#spread
        self._wait_seconds = random_delay(self._config.wait_ms / 1000)
        self._ring_confirm_seconds = random_reaction(RING_CONFIRM_SECONDS)
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
        """True while a ring waits for HP, a press's loot lands, or a far ring waits."""
        return self.loot_first(snap) or self._ring is not None or self._far_ring is not None

    def loot_first(self, snap: WorldState) -> bool:
        """True while a press's rings go and HP allows it: before the next attack.

        (bot.log 2026-11-04: the fight walked off right after the loot, and
        nothing was looted.)
        """
        now = self._clock()
        if snap.selected_target is not None:
            self._target_hp = snap.selected_target.hp_percent
            pc, pr = PLAYER_SLOT
            self._target_near = any(
                max(abs(c - pc), abs(r - pr)) <= 1 for c, r in snap.creature_slots
            )
        elif self._target_hp is not None:
            if self._target_hp <= KILL_HP:
                self._kill_wait_until = now + random_delay(KILL_RING_WAIT)
                self._kill_press = self._target_near
                self._kill_presses = 0
            self._target_hp, self._target_near = None, False
        busy = (
            self._ring is not None
            or self._kill_press
            or self._second_press_at is not None
            or self._checking
            or now < self._kill_wait_until
            or self._holding(now, snap.position)
        )
        return busy and not self._hp_too_low(snap)

    def step(self, snap: WorldState) -> bool:
        """Stand still while the loot lands, else step toward the nearest far ring."""
        if not (snap.client_focused and snap.screen_ok):
            return False
        now = self._clock()
        if self._ring is None and not self._holding(now, snap.position) and self._far_ring:
            if self._steps >= self._step_budget:
                log.warning("looting: ring at %s not reached, left here", self._far_ring.point)
                self._given_up_at, self._far_ring, self._steps = snap.position, None, 0
                return False
            if now >= self._next_step_at:
                assert self._game_region is not None
                (rx, ry), (px, py) = self._far_ring.point, slot_ring(
                    self._game_region, PLAYER_SLOT
                ).point
                key = WALK_KEYS[((rx > px) - (rx < px), (ry > py) - (ry < py))]
                log.info(
                    "looting: ring at %s %d tiles away: step %s",
                    self._far_ring.point,
                    self._far_ring.tiles_away,
                    key,
                )
                self._input.press_combo(key)
                self._next_step_at = now + random_delay(RING_STEP_SECONDS)
                self._steps += 1
            return True
        return self.should_loot(snap)  # stand still: the loot lands, or HP comes back
