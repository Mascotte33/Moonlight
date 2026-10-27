"""Cavebot movement (T22).

Waypoint execution: walk/rope/shovel/ladder/stairs/label/goto/check, stuck detection.
Waypoint format: ``data/waypoints/README.md``.

How a route is executed (one :meth:`Cavebot.step` per 20 Hz loop tick,
wired into ``loop.py`` as ``move_provider=lambda _s: bot.is_active()`` and
``move_action=bot.step``):

* **Position**: read from ``WorldState.position`` -- perception owns the
  one minimap tracker (seeded with an absolute start position, F-03); the
  cavebot never captures or tracks on its own. ``(0, 0, 0)`` (unknown)
  means no action this tick (fail closed); the stuck timer keeps running.
* **Walking**: the minimap is 1 px = 1 tile with the player at its center,
  so the bot clicks the minimap at ``center + (dx, dy)`` (clamped to the
  radar) and lets the client's auto-walk path there. The click is re-issued
  only after the player has stood still for ``reclick_interval`` seconds.
  With ``walk_keys`` on, the bot holds one arrow key per step instead:
  the first step of a shortest path on the walk-cost map
  (:mod:`moonlight.cavebot.pathing`), or straight at the waypoint without
  map data. The key stays down while the next step goes the same way, so
  the client walks on without a pause; a turn lets go of it and holds the
  new one, and standing still (a target, stuck, a floor change, stop)
  lets go. The next key goes once the position changed, or after
  ``_KEY_RETRY`` seconds. A key that did not move the player is pressed
  once more (a lag looks the same); a second miss marks its target tile
  blocked (a player, an NPC, a creature) for ``_BLOCK_TTL``
  seconds and the path is searched again around every blocked tile. The
  sidestep below is for minimap clicks only.
* **Creatures** (``walk_keys``): every arrow key is preceded by a
  health-bar scan of the game grid (:mod:`moonlight.perception.creatures`);
  creature tiles are walked around when that costs at most ``_DETOUR`` more
  steps (no guess where a creature steps next: it closed 2-wide corridors).
  The view scrolls for ``_VIEW_SETTLE`` after a step: on the tick the
  position changed it still shows the previous tile, so the scan is mapped
  from there; later ticks in the scroll keep that scan. The path search
  also steps diagonally (Q/E/Z/C) where that is the cheaper way past.
  **Stuck** means no way around the creatures and bumped tiles, or a way
  around over ``_DETOUR`` steps longer, with monsters in the battle list,
  within ``_STUCK_WINDOW`` seconds of a bump (before one it walks the short
  way and bumps first: the scan can be a tile off): it sets
  :attr:`Cavebot.stuck_on_creatures` and stands still; the loop then
  targets with every monster attacking (spells and area rules included)
  and clears the flag after each kill, so the cavebot looks for a way again.
  While a target is selected it stands still, whatever selected it, and
  walks on once nothing is. Stuck after a bump with nothing to fight and no
  way over tiles not bumped yet: the waypoint is skipped, and the next ones
  in route order, until one on this floor has such a way; none: alarm
  ``"stuck"``.
* **Arrival**: within 1 tile (Chebyshev distance) of the waypoint on the
  same floor; ``node`` within 3 tiles, ``stand`` only on the exact tile.
  ``walk``/``node``/``stand``/``lure`` are then done (``lure`` walks with
  attacking off, see :meth:`Cavebot.luring`); ``rope``/``shovel``/``ladder`` click
  the waypoint's tile in the game grid (15x11 tiles, player at slot (7, 5))
  and wait for the expected floor change (rope: z - 1, shovel: z + 1,
  ladder: up or down, so a sewer grate is a ``ladder`` too), retrying
  every ``reclick_interval``. ``stairs`` walks onto the
  exact tile and is done when the floor changes, up or down.
* **label / goto**: positionless. ``label`` is a jump target; ``goto``
  jumps to the waypoint after the named label (route loops).
* **check**: positionless. Jumps like ``goto`` when refill is due (used cap
  at or over ``refill_cap``, or :attr:`Cavebot.refill_now` set, which the
  jump clears); otherwise falls through. The town trip is a section behind
  a label that ends in a ``goto`` back to the hunt.
* **End of route**: a hunting route starts over at waypoint 0; a route
  with ``loop`` off completes and the cavebot stops.
* **Blocked** (an NPC in the way): after ``sidestep_after`` seconds without
  moving, the bot clicks (or with ``walk_keys`` steps onto) the tile left of
  its walking direction, then
  ``sidestep_after`` later the tile right of it. Once it moves again after a
  sidestep, a ``walk`` waypoint is skipped; any other waypoint is retried.
* **Stuck**: position unchanged for ``stuck_timeout`` seconds -> the route
  stops and :attr:`Cavebot.alarm` is set to ``"stuck"``. The loop calls
  :meth:`Cavebot.resume` whenever MOVE is (re)entered, so time spent in a
  fight/loot/pause never counts as "stuck" (F-06).
* **Unexpected floor change** (z differs from what the route expects): the
  route re-syncs to the next waypoint on the new floor, or stops with
  alarm ``"lost"`` when no waypoint is on that floor.

Runtime failures never raise from :meth:`step` (an exception would kill
the loop); they stop the route and set :attr:`alarm`.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from moonlight.cavebot.pathing import CostMap, next_step
from moonlight.core.state import Position, WorldState
from moonlight.hardware.clicker import ClickController
from moonlight.hardware.input import ClickSide, InputBackend
from moonlight.perception.creatures import PLAYER_SLOT, find_creatures
from moonlight.perception.floor import MAX_FLOOR, MIN_FLOOR
from moonlight.perception.minimap import Rect

__all__ = [
    "ACTIONS",
    "GAME_GRID_COLS",
    "GAME_GRID_ROWS",
    "Cavebot",
    "Route",
    "RouteError",
    "RouteFileLoader",
    "Waypoint",
]

logger = logging.getLogger(__name__)

#: Valid waypoint actions.
ACTIONS = (
    "walk", "node", "stand", "lure", "rope", "shovel", "ladder", "stairs", "label", "goto", "check",
)

#: Actions that need no position (executed immediately).
_JUMP_ACTIONS = ("label", "goto", "check")

#: Floor change each tile action causes (0 = sky, 15 = deepest).
_FLOOR_DELTA = {"rope": -1, "ladder": -1, "shovel": +1}

#: Arrival distance in tiles; every other positional action uses 1.
_REACH = {"node": 3, "stand": 0, "stairs": 0}
# doc: docs/prs/keys-walking.md#arrow-steps
# doc: docs/prs/keys-walking.md#learned-blocks
#: Walk key per one-tile step (y grows downward, up is north); numpad keys walk diagonally.
WALK_KEYS = {
    (0, -1): "up", (0, 1): "down", (-1, 0): "left", (1, 0): "right",
    (-1, -1): "q", (1, -1): "e", (-1, 1): "z", (1, 1): "c",
}  # fmt: skip
#: Seconds without moving before an arrow key is pressed again.
_KEY_RETRY = 0.7
#: Outlasts bumping all 8 neighbours (2 presses each); a creature walking off frees its tile.
_BLOCK_TTL = 12.0
_STUCK_WINDOW = 5.0
#: The game view scrolls a step after the position changed: a scan then sees creatures a tile off.
_VIEW_SETTLE = 0.5
_DETOUR = 8  # extra steps worth walking around creatures; longer -> fight through

#: Game grid layout (docs/ui_layout.md #10): 15x11 tiles, player at (7, 5).
GAME_GRID_COLS = 15
GAME_GRID_ROWS = 11


class RouteError(ValueError):
    """A route file is missing, unreadable or malformed."""


@dataclass(frozen=True)
class Waypoint:
    """One route step. ``label`` names a label / goto target."""

    x: int
    y: int
    z: int
    action: str = "walk"
    label: str = ""


@dataclass
class Route:
    """A named list of waypoints plus the index of the one being executed."""

    name: str
    description: str = ""
    waypoints: list[Waypoint] = field(default_factory=list)
    #: Start over at waypoint 0 after the last one (off: the route completes).
    loop: bool = True
    #: Where the bot is on the route, not part of the route itself.
    current: int = field(default=0, compare=False)


class RouteFileLoader:
    """Load and validate ``data/waypoints/<route>.json`` files."""

    @staticmethod
    def load(path: str | Path) -> Route:
        """Load a route file.

        Raises:
            RouteError: If the file is missing, not JSON, or invalid.
        """
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RouteError(f"cannot read route {path}: {exc}") from exc
        return RouteFileLoader.parse(raw)

    @staticmethod
    def parse(raw: object) -> Route:
        """Validate an already-decoded route object.

        Raises:
            RouteError: On any format violation.
        """
        if not isinstance(raw, dict):
            raise RouteError("route must be a JSON object")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise RouteError("route 'name' must be a non-empty string")
        description = raw.get("description", "")
        if not isinstance(description, str):
            raise RouteError("route 'description' must be a string")
        items = raw.get("waypoints")
        if not isinstance(items, list) or not items:
            raise RouteError("route 'waypoints' must be a non-empty list")

        waypoints = [RouteFileLoader._waypoint(i, item) for i, item in enumerate(items)]
        if all(wp.action in _JUMP_ACTIONS for wp in waypoints):
            raise RouteError("route needs at least one positional waypoint")
        labels = [wp.label for wp in waypoints if wp.action == "label"]
        if len(labels) != len(set(labels)):
            raise RouteError("duplicate label names")
        for i, wp in enumerate(waypoints):
            if wp.action in ("goto", "check") and wp.label not in labels:
                raise RouteError(f"waypoint {i}: {wp.action} target {wp.label!r} has no label")
        return Route(name=name, description=description, waypoints=waypoints)

    @staticmethod
    def _waypoint(index: int, item: object) -> Waypoint:
        if not isinstance(item, dict):
            raise RouteError(f"waypoint {index}: must be an object")
        action = item.get("action", "walk")
        if action not in ACTIONS:
            raise RouteError(f"waypoint {index}: action must be one of {ACTIONS}, got {action!r}")
        label = item.get("label", "")
        if action in _JUMP_ACTIONS:
            if not isinstance(label, str) or not label:
                raise RouteError(f"waypoint {index}: {action} needs a non-empty 'label'")
            return Waypoint(0, 0, 0, action, label)
        coords = []
        for key in ("x", "y", "z"):
            value = item.get(key)
            # bool is an int subclass; reject it explicitly.
            if not isinstance(value, int) or isinstance(value, bool):
                raise RouteError(f"waypoint {index}: {key!r} must be an int")
            coords.append(value)
        if not MIN_FLOOR <= coords[2] <= MAX_FLOOR:
            raise RouteError(f"waypoint {index}: z must be {MIN_FLOOR}..{MAX_FLOOR}")
        return Waypoint(coords[0], coords[1], coords[2], action, "")


class Cavebot:
    """Waypoint runner driven once per tick by ``loop.py``'s MOVE state."""

    def __init__(
        self,
        route: Route,
        minimap_region: Rect,
        clicker: ClickController,
        state: WorldState,
        *,
        game_region: Rect,
        input_backend: InputBackend | None = None,
        rope_hotkey: str = "F9",
        shovel_hotkey: str = "F10",
        stuck_timeout: float = 60.0,
        reclick_interval: float = 1.0,
        sidestep_after: float = 3.0,
        walk_keys: bool = False,
        cost_map: CostMap | None = None,
        frames: Callable[[], np.ndarray] | None = None,
        clock: Callable[[], float] | None = None,
        on_waypoint_reached: Callable[[int], None] | None = None,
        refill_cap: float | None = None,
    ) -> None:
        """Create the runner.

        Args:
            route: The route to execute.
            minimap_region: The ``minimap`` region (walk-click target).
            clicker: Closed-loop clicker for minimap and game-grid clicks.
            state: World state: the position source; the bot does nothing
                unless ``screen_ok``.
            game_region: The ``game_grid`` region ``(x, y, w, h)``.
            input_backend: Presses the rope/shovel hotkeys and the walk
                arrows; required for rope or shovel waypoints and for
                *walk_keys*.
            rope_hotkey: Combo bound to "use rope" in the client.
            shovel_hotkey: Combo bound to "use shovel" in the client.
            stuck_timeout: Seconds without movement before alarm "stuck".
            reclick_interval: Seconds standing still before a walk click or
                tile action is retried.
            sidestep_after: Seconds without moving before each of the two
                sidestep clicks around a blocker.
            walk_keys: Walk with the arrow keys instead of minimap clicks.
            cost_map: Walkable tiles for *walk_keys* path finding; without
                it the bot steps straight at the waypoint.
            frames: The current screenshot, for the creature scan; without
                it creatures are found only by bumping into them.
            clock: Monotonic-clock override (tests).
            on_waypoint_reached: Called with the index of each reached
                positional waypoint.
            refill_cap: Used capacity % at which a ``check`` jumps; None
                (Refill off) jumps only on :attr:`refill_now`.

        Raises:
            ValueError: On bad timings, or rope/shovel waypoints or
                *walk_keys* without an input backend.
        """
        self._minimap_region: Rect = minimap_region
        if stuck_timeout <= 0 or reclick_interval <= 0:
            raise ValueError("stuck_timeout and reclick_interval must be > 0")
        needs_keys = any(wp.action in ("rope", "shovel") for wp in route.waypoints)
        if needs_keys and input_backend is None:
            raise ValueError("rope/shovel waypoints need an input_backend for hotkeys")
        if walk_keys and input_backend is None:
            raise ValueError("walk_keys needs an input_backend for the arrow keys")
        self.route = route
        self._clicker = clicker
        self._state = state
        self._game_region = game_region
        self._input = input_backend
        self._hotkeys = {"rope": rope_hotkey, "shovel": shovel_hotkey}
        self._stuck_timeout = float(stuck_timeout)
        self._reclick_interval = float(reclick_interval)
        self._sidestep_after = float(sidestep_after)
        self._walk_keys = walk_keys
        self._cost_map = cost_map
        self._blocked: dict[tuple[int, int, int], float] = {}  # tile -> expiry
        self._target: tuple[int, int, int] | None = None
        #: The target tile whose key was pressed a second time after not moving.
        self._retried_target: tuple[int, int, int] | None = None
        #: The walk key held down, kept between steps so the client walks on.
        self._held_walk_key: str | None = None
        self._frames = frames
        #: Creatures leave no way through: the loop targets them (cleared after each kill).
        self.stuck_on_creatures = False
        self._stuck_allowed_until = float("-inf")
        self._creatures: set[tuple[int, int]] = set()  # tiles the last scan saw
        self._previous_pos: Position | None = None  # where the view still is right after a step
        #: Bumped tiles a scan saw a creature on: free again once it walks off.
        self._bumped_into_creature: set[tuple[int, int, int]] = set()
        self._clock = clock if clock is not None else time.monotonic
        self._callback = on_waypoint_reached
        self.refill_cap = refill_cap
        #: Refill now: the next ``check`` jumps to town (cleared by the jump).
        self.refill_now = False

        #: Why the route stopped on its own ("stuck", "lost", "goto_loop"),
        #: or None.
        self.alarm: str | None = None
        self._active = False
        self._last_pos: Position | None = None
        self._last_move = 0.0
        #: Last position change (resume() also restarts _last_move, a fight is not "stuck").
        self._moved_at = float("-inf")
        self._last_cmd = 0.0
        self._expected_z: int | None = None
        #: Sidestep clicks sent since the player last moved (0-2).
        self._sidesteps = 0
        self._reset_tracking()

    # -- public API ---------------------------------------------------------

    def start(self, nearest: bool = False) -> None:
        """Begin the route (clears any alarm).

        Args:
            nearest: Resume at the positional waypoint nearest to the
                current position on the current floor (e.g. after a refill
                trip, F-25) instead of waypoint 0.
        """
        self.route.current = self._nearest_waypoint() if nearest else 0
        self.alarm = None
        self._active = True
        self._reset_tracking()

    def go_to(self, index: int) -> None:
        """Walk to waypoint *index* next (picked in the GUI's waypoint list)."""
        # ponytail: set from the GUI thread while the loop steps; one int write, no lock
        self.route.current = index
        self.alarm = None
        self._active = True
        self._clicker.cancel()  # the pending click targeted the old waypoint
        self._reset_tracking()
        self._resolve_jumps()

    def resume(self) -> None:
        """MOVE (re)entered: restart the stuck timer and re-click at once."""
        self._last_move = self._clock()
        self._last_cmd = float("-inf")
        self._sidesteps = 0
        self._clicker.cancel()  # a click queued before leaving MOVE is stale

    def stop(self) -> None:
        """Stop the route (no further input is sent)."""
        self._active = False
        self.stuck_on_creatures = False
        self._clicker.cancel()
        self.release_walk_key()

    def release_walk_key(self) -> None:
        """Let go of the held walk key: the player stops after this step."""
        if self._held_walk_key is not None:
            assert self._input is not None  # a key is held only with walk_keys
            self._input.release_combo(self._held_walk_key)
            self._held_walk_key = None

    def is_active(self) -> bool:
        """True while the route is running."""
        return self._active

    def luring(self) -> bool:
        """True while walking to a ``lure`` waypoint: the loop holds attacks."""
        wps = self.route.waypoints
        return (
            self._active
            and self.route.current < len(wps)
            and wps[self.route.current].action == "lure"
        )

    def on_waypoint_reached(self, index: int) -> None:
        """Hook called when positional waypoint *index* is reached."""
        logger.debug("cavebot: reached waypoint %d", index)
        if self._callback is not None:
            self._callback(index)

    def step(self) -> None:
        """Run one tick of the route (see the module docstring)."""
        if not self._active or not self._state.screen_ok:
            return
        now = self._clock()
        pos = self._read_position()
        moved_aside = False
        if pos is not None and pos != self._last_pos:
            moved_aside = self._sidesteps > 0
            self._sidesteps = 0
            self._previous_pos, self._last_pos = self._last_pos, pos
            self._last_move = self._moved_at = now
        elif now - self._last_move > self._stuck_timeout:
            self._halt("stuck")
            return
        if pos is None or not self._resolve_jumps():
            self.release_walk_key()
            return

        wp = self.route.waypoints[self.route.current]
        if moved_aside and wp.action == "walk":
            logger.info("cavebot: got past a blocker, skipping waypoint %d", self.route.current)
            self._advance()
            return
        if self._expected_z is not None and pos.z == self._expected_z:
            self._advance()  # the rope/shovel/ladder action worked
            return
        if pos.z != wp.z and wp.action in ("stairs", "ladder"):
            self._advance()  # the stairs, ladder or sewer grate changed the floor
            return
        if pos.z != wp.z:
            self.release_walk_key()
            self._resync_floor(pos.z)
            return
        if max(abs(wp.x - pos.x), abs(wp.y - pos.y)) <= _REACH.get(wp.action, 1):
            if wp.action not in _FLOOR_DELTA:
                self._advance()
            else:
                self.release_walk_key()
                self._expected_z = wp.z + _FLOOR_DELTA[wp.action]
                self._retry_if_idle(now, lambda: self._tile_action(wp, pos))
            return
        if (
            not self._walk_keys
            and self._sidesteps < 2
            and now - self._last_move >= self._sidestep_after * (self._sidesteps + 1)
        ):
            self._sidestep(wp, pos)
            self._last_cmd = now
            return
        if not self._walk_keys:
            self._retry_if_idle(now, lambda: self._walk_toward(wp, pos))
        elif self._state.selected_target is not None and not self.luring():
            self._target = None  # stand still while a target is selected
            self.release_walk_key()
            self._stuck_allowed_until = now + _STUCK_WINDOW  # the fight may have closed the way
        elif self._last_move > self._last_cmd or now - self._last_cmd >= _KEY_RETRY:
            target = self._target if self._last_move <= self._last_cmd else None
            step = None if target is None else (target[0] - pos.x, target[1] - pos.y)
            if step is not None and target != self._retried_target and step in WALK_KEYS:
                self._retried_target = target  # a lag looks like a block: press once more
                self._press_walk_key(step, again=True)
            else:
                if target is not None:
                    logger.info("cavebot: tile %s,%s,%s blocked, walking around", *target)
                    self._blocked[target] = now + _BLOCK_TTL
                    self._stuck_allowed_until = now + _STUCK_WINDOW
                self._retried_target = None
                self._step_toward(wp, pos, now)
            self._last_cmd = now

    # -- internals ----------------------------------------------------------

    def _reset_tracking(self) -> None:
        self._last_pos = None
        self._last_move = self._clock()
        self._last_cmd = float("-inf")
        self._expected_z = None
        self._sidesteps = 0
        self.stuck_on_creatures = False

    def _halt(self, alarm: str) -> None:
        logger.warning("cavebot: route %r stopped: %s", self.route.name, alarm)
        self.alarm = alarm
        self._active = False
        self.stuck_on_creatures = False
        self._clicker.cancel()
        self.release_walk_key()

    def _read_position(self) -> Position | None:
        pos = self._state.snapshot().position
        return None if pos == Position(0, 0, 0) else pos

    def _nearest_waypoint(self) -> int:
        """Index of the positional waypoint closest to us on our floor (else 0)."""
        pos = self._read_position()
        if pos is None:
            return 0
        candidates = [
            (max(abs(wp.x - pos.x), abs(wp.y - pos.y)), i)
            for i, wp in enumerate(self.route.waypoints)
            if wp.action not in _JUMP_ACTIONS and wp.z == pos.z
        ]
        return min(candidates)[1] if candidates else 0

    def _resolve_jumps(self) -> bool:
        """Skip labels / follow gotos; False when the route ended or looped."""
        index = self._positional_from(self.route.current)
        if index is None:
            if self._active:
                logger.info("cavebot: route %r complete", self.route.name)
                self._active = False
            return False
        self.route.current = index
        return True

    def _positional_from(self, index: int) -> int | None:
        """The first positional waypoint from *index* on, following labels and gotos.

        None at the end of a route with ``loop`` off, or after a goto loop
        (which halts the route).
        """
        wps = self.route.waypoints
        for _ in range(len(wps) + 1):
            if index >= len(wps):
                if not self.route.loop:
                    return None
                index = 0
                continue
            wp = wps[index]
            if wp.action == "label":
                index += 1
            elif wp.action == "goto" or (wp.action == "check" and self._refill_due()):
                if wp.action == "check":
                    # ponytail: _skip_unreachable's lookahead also lands here and
                    # can use up refill_now; press Refill now again if it does.
                    logger.info("cavebot: refill due, jump to %r", wp.label)
                    self.refill_now = False
                index = next(
                    i + 1 for i, w in enumerate(wps) if w.action == "label" and w.label == wp.label
                )
            elif wp.action == "check":
                index += 1
            else:
                return index
        self._halt("goto_loop")
        return None

    # doc: docs/prs/refill.md#check-waypoint
    def _refill_due(self) -> bool:
        cap = self._state.snapshot().capacity
        full = self.refill_cap is not None and cap.max_weight > 0 and cap.percent >= self.refill_cap
        return self.refill_now or full

    # doc: docs/prs/walking.md#skip-unreachable
    def _skip_unreachable(self, pos: Position, walkable: Callable[[int, int, int], bool]) -> None:
        """Move on to the next waypoint with a path over *walkable*; none: alarm "stuck"."""
        start, index = (pos.x, pos.y, pos.z), self.route.current
        for _ in range(len(self.route.waypoints)):
            logger.info("cavebot: waypoint %d unreachable, skipping", index)
            following = self._positional_from(index + 1)
            if following is None or following == self.route.current:
                break
            index = following
            wp = self.route.waypoints[index]
            goal = (wp.x, wp.y, wp.z)
            if wp.z == pos.z and (goal == start or next_step(start, goal, walkable) is not None):
                self.route.current = index
                self._target = self._retried_target = None
                return
        if self._active:  # a goto loop already halted it
            self._halt("stuck")

    def _advance(self) -> None:
        self.on_waypoint_reached(self.route.current)
        self.route.current += 1
        self._expected_z = None
        self._last_cmd = float("-inf")
        self._clicker.cancel()  # the pending click targeted the old waypoint
        self._resolve_jumps()

    def _resync_floor(self, z: int) -> None:
        """Jump to the next positional waypoint on floor *z* (wrapping)."""
        wps = self.route.waypoints
        n = len(wps)
        for k in range(n):
            i = (self.route.current + k) % n
            if wps[i].action not in _JUMP_ACTIONS and wps[i].z == z:
                logger.info("cavebot: floor changed to %d, resync to waypoint %d", z, i)
                self.route.current = i
                self._expected_z = None
                self._last_cmd = float("-inf")
                self._clicker.cancel()
                return
        self._halt("lost")

    def _retry_if_idle(self, now: float, command: Callable[[], None]) -> None:
        """Send *command* first time, then only after standing still a while."""
        if self._clicker.busy:
            self._clicker.step()  # P5: one capture + one move per tick
            return
        idle_since = max(self._last_cmd, self._last_move)
        if self._last_cmd == float("-inf") or now - idle_since >= self._reclick_interval:
            command()
            self._last_cmd = now

    def _walk_toward(self, wp: Waypoint, pos: Position) -> None:
        mx, my, mw, mh = self._minimap_region
        # Clamp to the radar (keep 2 px off the edge, minimap.py marker check).
        dx = max(-(mw // 2 - 2), min(mw // 2 - 2, wp.x - pos.x))
        dy = max(-(mh // 2 - 2), min(mh // 2 - 2, wp.y - pos.y))
        self._clicker.request((mx + mw // 2 + dx, my + mh // 2 + dy))

    def _step_toward(self, wp: Waypoint, pos: Position, now: float) -> None:
        """Press the walk key of the next step to *wp* around blocked tiles.

        Without map data every tile but the blocked ones counts as walkable.
        Stuck (no way around the creatures and bumped tiles, or the way
        around costs over ``_DETOUR`` more) with monsters in the battle list:
        it sets :attr:`stuck_on_creatures` and waits. Otherwise it tries
        tiles not bumped yet, and with no way over them skips the waypoint.
        """
        self._blocked = {t: end for t, end in self._blocked.items() if end > now}
        cost = self._cost_map if self._cost_map is not None and not self._cost_map.empty else None
        if self._scan(pos, now):
            self._release_tiles_creatures_left()

        def free(x: int, y: int, z: int) -> bool:  # a creature on a bumped tile can be killed
            return ((x, y, z) not in self._blocked or (x, y) in self._creatures) and (
                cost is None or cost.walkable(x, y, z)
            )

        def walkable(x: int, y: int, z: int) -> bool:
            return (x, y) not in self._creatures and free(x, y, z)

        def not_bumped(x: int, y: int, z: int) -> bool:
            return (x, y, z) not in self._blocked and (cost is None or cost.walkable(x, y, z))

        start, goal = (pos.x, pos.y, pos.z), (wp.x, wp.y, wp.z)
        path = next_step(start, goal, walkable)
        detour_too_long = False
        if path is not None and self._creatures:
            short = next_step(start, goal, free)
            detour_too_long = short is not None and path[1] > short[1] + _DETOUR
        stuck = path is None or detour_too_long
        if stuck and self._state.monsters and now < self._stuck_allowed_until:
            if not self.stuck_on_creatures:
                logger.info("cavebot: creatures block the way, targeting takes over")
            self.stuck_on_creatures = True
            self._target = None
            self.release_walk_key()
            self._stuck_allowed_until = now + _STUCK_WINDOW  # stay put while it fights
            return
        self.stuck_on_creatures = False
        if stuck and now >= self._stuck_allowed_until:
            path = next_step(start, goal, free)  # no bump yet: walk into it, then react
        elif stuck:
            # Nothing to fight: the scan can be a tile off, so try every tile not bumped yet.
            path = next_step(start, goal, not_bumped)
            if path is None:
                self.release_walk_key()
                self._skip_unreachable(pos, not_bumped)
                return
        if path is None:
            step = self._best_unbumped_step(wp, pos)
        else:
            step = path[0]
        self._target = (pos.x + step[0], pos.y + step[1], pos.z)
        self._press_walk_key(step)

    def _release_tiles_creatures_left(self) -> None:
        """A bumped tile is free again once the creature that stood on it walked off."""
        self._bumped_into_creature &= self._blocked.keys()
        for tile in list(self._blocked):
            if (tile[0], tile[1]) in self._creatures:
                self._bumped_into_creature.add(tile)
            elif tile in self._bumped_into_creature:
                del self._blocked[tile]
                self._bumped_into_creature.discard(tile)

    def _best_unbumped_step(self, wp: Waypoint, pos: Position) -> tuple[int, int]:
        """No path: the step nearest *wp*, unbumped tiles first (map data can be wrong)."""

        def rank(step: tuple[int, int]) -> tuple[bool, int, int]:
            x, y = pos.x + step[0], pos.y + step[1]
            dx, dy = abs(wp.x - x), abs(wp.y - y)
            return (x, y, pos.z) in self._blocked, max(dx, dy), dx + dy

        return min(WALK_KEYS, key=rank)

    def _scan(self, pos: Position, now: float) -> bool:
        """Refresh the creature tiles from the game grid (see the module docstring).

        Returns whether a frame was scanned.
        """
        scrolling = now - self._moved_at < _VIEW_SETTLE
        if scrolling and now != self._moved_at:
            return False  # mid-step: keep the last scan (its tiles are absolute)
        frame = self._frames() if self._frames is not None else None
        if frame is None:
            self._creatures = set()
            return False
        # ponytail: the first tick after a step counts as unscrolled; a slow one is a tile off
        view = self._previous_pos if scrolling and self._previous_pos is not None else pos
        px, py = view.x - PLAYER_SLOT[0], view.y - PLAYER_SLOT[1]
        self._creatures = {(px + c, py + r) for c, r in find_creatures(frame, self._game_region)}
        return True

    def _press_walk_key(self, step: tuple[int, int], again: bool = False) -> None:
        """Hold the key of *step*; *again* lets go first for a fresh key press.

        A held key keeps the client walking, and the key for the next step is
        down when the current step ends: a tap sent mid-step is dropped.
        """
        assert self._input is not None  # checked in __init__
        key = WALK_KEYS[step]
        if again or key != self._held_walk_key:
            self.release_walk_key()
        self._input.hold_combo(key)
        self._held_walk_key = key

    def _sidestep(self, wp: Waypoint, pos: Position) -> None:
        """Step to the tile beside the walking direction: left first, then right."""
        sx = (wp.x > pos.x) - (wp.x < pos.x)
        sy = (wp.y > pos.y) - (wp.y < pos.y)
        side = (sy, -sx) if self._sidesteps == 0 else (-sy, sx)  # y grows downward
        self._sidesteps += 1
        logger.info("cavebot: blocked, sidestep %d to %+d,%+d", self._sidesteps, *side)
        mx, my, mw, mh = self._minimap_region
        self._clicker.cancel()
        self._clicker.request((mx + mw // 2 + side[0], my + mh // 2 + side[1]))

    def _tile_action(self, wp: Waypoint, pos: Position) -> None:
        gx, gy, gw, gh = self._game_region
        tw, th = gw // GAME_GRID_COLS, gh // GAME_GRID_ROWS
        col = PLAYER_SLOT[0] + wp.x - pos.x
        row = PLAYER_SLOT[1] + wp.y - pos.y
        tile = (gx + col * tw + tw // 2, gy + row * th + th // 2)
        if wp.action == "ladder":
            self._clicker.request(tile, side=ClickSide.RIGHT)  # "use" the ladder
            return
        assert self._input is not None  # checked in __init__
        self._input.press_combo(self._hotkeys[wp.action])
        self._clicker.request(tile)
        if wp.action == "shovel":
            # Steps into the hole after digging; the clicker drops this
            # follow-up when the dig click fails. ponytail: add a delay if
            # the client drops the walk click during the dig animation.
            self._clicker.request(tile)
