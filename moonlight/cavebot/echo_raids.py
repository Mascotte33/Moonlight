"""Echo raid triggers on the player's floor: avoided, or walked to and clicked.

The cavebot's settled scans feed :meth:`EchoRaids.see`; its walk asks
:meth:`EchoRaids.step` each tick where to go instead of the waypoint. Their
tiles (:attr:`EchoRaids.tiles`) are never walked over, and a waypoint on one
is skipped. With *trigger* on, once no fight is on and HP is at
``ECHO_RAID_MIN_HP`` or above, the bot walks next to the nearest trigger
whose sprite turns, right-clicks it, and ``ECHO_RAID_CLICK_WAIT`` later,
still there, left-clicks it (the character steps on). When it is gone
(triggered, or someone else did) the route goes on from the nearest
waypoint. After ``ECHO_RAID_TRIES`` such tries, or with no way to it, it is
ignored until it was off the screen for ``ECHO_RAID_FORGET`` seconds; a
trigger that never triggered is a dud (a coffin, a lamp) for good.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from moonlight.cavebot.route import Waypoint, has_tile
from moonlight.core.random_delay import random_delay
from moonlight.core.state import Position
from moonlight.hardware.clicker import ClickController
from moonlight.hardware.input import ClickSide
from moonlight.perception.echo_raid import find_echo_raids
from moonlight.perception.minimap import Rect

__all__ = [
    "ECHO_RAID_AFTER_WAIT",
    "ECHO_RAID_CLICK_WAIT",
    "ECHO_RAID_DUDS_FILE",
    "ECHO_RAID_FORGET",
    "ECHO_RAID_MIN_HP",
    "ECHO_RAID_SHOTS_DIR",
    "ECHO_RAID_TRIES",
    "EchoRaids",
    "EchoStep",
    "load_duds",
    "turned",
]

logger = logging.getLogger(__name__)

Tile = tuple[int, int]
Tile3 = tuple[int, int, int]

# doc: docs/prs/echo-raid.md#use
#: HP % at or above which an echo raid trigger is walked onto (with no monster around).
ECHO_RAID_MIN_HP = 50.0
#: Seconds after each click on an echo raid trigger before looking whether it is gone.
ECHO_RAID_CLICK_WAIT = 1.0
#: Right-click + left-click tries on one trigger before it is ignored.
ECHO_RAID_TRIES = 3
#: Seconds off the screen after which an ignored trigger counts as new.
ECHO_RAID_FORGET = 30.0
#: Seconds the cavebot stands still after an echo raid next to the player is gone
#: (clicked or walked into); targeting and healing go on.
ECHO_RAID_AFTER_WAIT = 2.0
#: Screenshots 1 s after each echo raid click, the clicked pixel circled (bot.log names each).
ECHO_RAID_SHOTS_DIR = Path("logs") / "echo_raids"
#: An echo raid is clicked only once its sprite changed between two looks: at
#: least this share of its pixels ...
ECHO_RAID_MOTION_SHARE = 0.02
#: ... changed by more than this in some colour (a still coffin: 0).
ECHO_RAID_MOTION_LEVEL = 40
#: Tiles whose "echo raid" never triggered (a coffin, a lamp...): ``x y z`` per
#: line, never clicked again, also after a restart.
ECHO_RAID_DUDS_FILE = Path("data") / "echo_raid_duds.txt"


def load_duds() -> set[Tile3]:
    """The saved tiles whose echo raid never triggered."""
    try:
        lines = ECHO_RAID_DUDS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    duds = set()
    for line in lines:
        parts = line.split()
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            duds.add((int(parts[0]), int(parts[1]), int(parts[2])))
    return duds


def _save_dud(tile: Tile3) -> None:
    path = ECHO_RAID_DUDS_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write("{} {} {}\n".format(*tile))
    except OSError:
        logger.warning("cavebot: could not save %s", path)


def sprite_looks(
    frame: np.ndarray, region: Rect, tiles: set[Tile], px: int, py: int
) -> dict[Tile, np.ndarray]:
    """Each trigger's sprite on *frame* (2x2 tiles up and to the left of its
    tile); *px*, *py* is the world tile of the grid's top-left slot."""
    gx, gy, gw, gh = region
    tw, th = gw / 15, gh / 11
    looks: dict[Tile, np.ndarray] = {}
    for tile in tiles:
        col, row = tile[0] - px, tile[1] - py
        x0, y0 = int(gx + (col - 1) * tw), int(gy + (row - 1) * th)
        x1, y1 = int(gx + (col + 1) * tw), int(gy + (row + 1) * th)
        if x0 < gx or y0 < gy or x1 > gx + gw or y1 > gy + gh:
            continue
        looks[tile] = frame[y0:y1, x0:x1].astype(np.int16)
    return looks


# doc: docs/prs/echo-raid.md#motion-check
def turned(before: dict[Tile, np.ndarray], after: dict[Tile, np.ndarray]) -> dict[Tile, float]:
    """The triggers whose sprite changed between two settled looks, with the
    share of pixels changed.

    The trigger's swirl turns; a still thing of the same colour (an orange
    coffin in Venore, 2026-11-22) never does, so it is never clicked.
    """
    moved = {}
    for tile, look in after.items():
        old = before.get(tile)
        if old is None or old.shape != look.shape:
            continue
        changed = float((np.abs(look - old).max(axis=2) > ECHO_RAID_MOTION_LEVEL).mean())
        if changed >= ECHO_RAID_MOTION_SHARE:
            moved[tile] = changed
    return moved


@dataclass(frozen=True)
class EchoStep:
    """What the cavebot does after :meth:`EchoRaids.step`."""

    #: Where to walk this tick; None: this tick's work is done.
    walk: Waypoint | None = None
    #: The goal changed: plan the next step this tick.
    replan: bool = False
    #: The trigger is gone: the route goes on from the nearest waypoint.
    resume: bool = False
    #: The waypoint is on a trigger: go on to the next one.
    skip: bool = False


class EchoRaids:
    """The triggers the scans saw, and the one being triggered.

    *pixel* maps a world tile to the frame pixel of its middle for a player
    at a position; *screen* grabs a frame for the click screenshots.
    """

    def __init__(
        self,
        trigger: bool,
        clicker: ClickController,
        screen: Callable[[], np.ndarray | None],
        release_walk_key: Callable[[], None],
        pixel: Callable[[Tile, Position], tuple[int, int]],
    ) -> None:
        self.trigger = trigger
        self._clicker = clicker
        self._screen = screen
        self._release_walk_key = release_walk_key
        self._pixel = pixel
        # doc: docs/prs/echo-raid.md#avoid
        #: Trigger tiles (x, y) on the player's floor the last scan saw.
        self.tiles: set[Tile] = set()
        #: Until then the cavebot stands still: a trigger next to the player went.
        self.wait_until = float("-inf")
        self._goal: Tile3 | None = None
        #: Last click on the goal (right, then left) and when, None before the first.
        self._click: ClickSide | None = None
        self._clicked_at = float("-inf")
        #: ``ECHO_RAID_CLICK_WAIT`` with a random spread, picked at each click.
        self._click_wait = ECHO_RAID_CLICK_WAIT
        self._clicked_pixel = (0, 0)
        self._tries = 0
        #: Triggers given up on (no way, or tries used up) -> when last seen.
        self._ignored: dict[Tile, float] = {}
        self._duds = load_duds()
        #: The last settled look at each sprite, and the ones seen turning.
        self._looks: dict[Tile, np.ndarray] = {}
        self._moving: set[Tile] = set()

    @property
    def goal(self) -> Tile3 | None:
        """The trigger being walked onto (x, y, z), or None."""
        return self._goal

    def forget(self) -> None:
        """No frame: nothing seen."""
        self.tiles = set()

    def see(
        self, frame: np.ndarray, region: Rect, px: int, py: int, pos: Position, now: float
    ) -> None:
        """Read the triggers from a settled *frame*; *px*, *py* is the world
        tile of the game grid's top-left slot."""
        tiles = {(px + c, py + r) for c, r in find_echo_raids(frame, region)}
        if not self.trigger:
            for tile in sorted(tiles - self.tiles):
                logger.info("cavebot: echo raid seen at %s,%s,%s, avoiding", *tile, pos.z)
        gone_next_to_player = [
            t for t in self.tiles - tiles if max(abs(t[0] - pos.x), abs(t[1] - pos.y)) <= 1
        ]
        if gone_next_to_player:
            # doc: docs/prs/near-targeting.md#echo-wait
            logger.info(
                "cavebot: echo raid at %s,%s,%s gone next to the player, waiting %g s",
                *gone_next_to_player[0], pos.z, ECHO_RAID_AFTER_WAIT,
            )  # fmt: skip
            self.wait_until = now + random_delay(ECHO_RAID_AFTER_WAIT)
        self.tiles = tiles
        looks = sprite_looks(frame, region, tiles, px, py)
        for tile, changed in turned(self._looks, looks).items():
            if tile not in self._moving:
                logger.info(
                    "cavebot: echo raid at %s,%s turns (%.0f %% changed)", *tile, changed * 100
                )
            self._moving.add(tile)
        self._looks = looks
        self._moving &= tiles
        for tile in tiles & self._ignored.keys():
            self._ignored[tile] = now
        self._ignored = {
            tile: seen for tile, seen in self._ignored.items() if now - seen < ECHO_RAID_FORGET
        }

    # doc: docs/prs/echo-raid.md#use
    def step(
        self,
        wp: Waypoint,
        pos: Position,
        now: float,
        *,
        fight: Callable[[], bool],
        hp: float,
        settled: bool,
        rescan: Callable[[], bool],
        index: int,
    ) -> EchoStep:
        """The trigger to walk next to, else *wp*.

        *fight*: a fight is on; *settled*: the view stopped scrolling;
        *rescan*: scan the screen again, False when it could not; *index*:
        *wp*'s place in the route (for the log).
        """
        goal = self._goal
        if goal is not None and goal[:2] not in self.tiles:
            return self._gone(goal)
        safe = self.trigger and not fight() and hp >= ECHO_RAID_MIN_HP
        if goal is not None and self._click is not None:
            # a spawn after a click: look first
            return self._click_goal(goal, pos, now, safe=safe, settled=settled, rescan=rescan)
        candidates = (self.tiles & self._moving) - self._ignored.keys()
        if self._duds:
            candidates = {t for t in candidates if (*t, pos.z) not in self._duds}
        replan = False
        if not safe:
            self._drop_goal()
        elif goal is None and candidates:
            x, y = min(candidates, key=lambda t: max(abs(t[0] - pos.x), abs(t[1] - pos.y)))
            goal = self._goal = (x, y, pos.z)
            logger.info("cavebot: echo raid seen at %s,%s,%s, triggering", *goal)
            replan = True
        goal = self._goal
        if goal is not None:
            if max(abs(goal[0] - pos.x), abs(goal[1] - pos.y)) <= 1:
                done = self._click_goal(goal, pos, now, safe=safe, settled=settled, rescan=rescan)
                return EchoStep(replan=replan or done.replan, resume=done.resume)
            return EchoStep(walk=Waypoint(*goal, "stand"), replan=replan)
        if (wp.x, wp.y) in self.tiles and wp.z == pos.z and has_tile(wp):
            logger.info(
                "cavebot: echo raid on waypoint %d at %s,%s,%s, skipping it",
                index, wp.x, wp.y, wp.z,
            )  # fmt: skip
            return EchoStep(skip=True)
        return EchoStep(walk=wp)

    def give_up(self, now: float) -> bool:
        """No way to the goal: ignore it. False when there is no goal."""
        goal = self._goal
        if goal is None:
            return False
        logger.info("cavebot: no way to the echo raid at %s,%s,%s, ignoring it", *goal)
        self._ignored[goal[:2]] = now
        self._drop_goal()
        return True

    def _click_goal(
        self,
        goal: Tile3,
        pos: Position,
        now: float,
        *,
        safe: bool,
        settled: bool,
        rescan: Callable[[], bool],
    ) -> EchoStep:
        """Right-click the trigger next to the player, then left-click it, 1 s apart."""
        self._release_walk_key()
        if self._clicker.busy:
            self._clicker.step()
            return EchoStep()
        if not settled:
            return EchoStep()  # mid-scroll a click or a look lands a tile off
        last_click = self._click
        if last_click is not None:
            waiting = now - self._clicked_at < self._click_wait
            if waiting or not rescan():
                return EchoStep()
            self._save_shot(goal, last_click)
            if goal[:2] not in self.tiles:
                return self._gone(goal)
        if not safe:
            self._drop_goal()
            return EchoStep()
        if last_click is ClickSide.LEFT:
            self._tries += 1
            if self._tries >= ECHO_RAID_TRIES:
                logger.info(
                    "cavebot: echo raid at %s,%s,%s did not trigger after %d tries, ignoring it",
                    *goal, ECHO_RAID_TRIES,
                )  # fmt: skip
                self._ignored[goal[:2]] = now
                _save_dud(goal)
                self._duds.add(goal)
                self._drop_goal()
                return EchoStep(replan=True)
        side = ClickSide.LEFT if last_click is ClickSide.RIGHT else ClickSide.RIGHT
        self._clicked_pixel = self._pixel(goal[:2], pos)
        self._clicker.request(self._clicked_pixel, side=side)
        self._click, self._clicked_at = side, now
        # doc: docs/prs/random-delays.md#spread
        self._click_wait = random_delay(ECHO_RAID_CLICK_WAIT)
        return EchoStep()

    def _save_shot(self, goal: Tile3, side: ClickSide) -> None:
        """Save the screen 1 s after a click on *goal*, the clicked pixel circled in yellow."""
        frame = self._screen()
        if frame is None:
            return
        picture = cv2.cvtColor(np.ascontiguousarray(frame), cv2.COLOR_RGB2BGR)
        cv2.circle(picture, self._clicked_pixel, 12, (0, 255, 255), 2)
        ECHO_RAID_SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp, click = time.strftime("%Y%m%d-%H%M%S"), side.name.lower()
        path = ECHO_RAID_SHOTS_DIR / f"{stamp}_{goal[0]}_{goal[1]}_{goal[2]}_{click}.png"
        if not cv2.imwrite(str(path), picture):
            raise OSError(f"could not save the echo raid screenshot {path}")
        logger.info("cavebot: echo raid %s-click screenshot: %s", click, path)

    def _gone(self, goal: Tile3) -> EchoStep:
        """The goal left the screen: the route goes on from the nearest waypoint."""
        how = "triggered" if self._click is not None else "gone"
        logger.info("cavebot: echo raid at %s,%s,%s %s, back to the nearest waypoint", *goal, how)
        self._drop_goal()
        return EchoStep(resume=True)

    def _drop_goal(self) -> None:
        self._goal = None
        self._click = None
        self._tries = 0
