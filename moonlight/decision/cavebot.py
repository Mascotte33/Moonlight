"""Cavebot movement (T22).

Waypoint execution: walk/rope/shovel/ladder/stairs/label/goto, stuck detection.
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
  With ``walk_keys`` on, the bot presses one arrow key per step instead:
  the first step of a shortest path on the walk-cost map
  (:mod:`moonlight.decision.pathing`), or straight at the waypoint without
  map data. The next key goes once the position changed, or after
  ``_KEY_RETRY`` seconds. A key that did not move the player marks its
  target tile blocked (a player, an NPC, a creature) for ``_BLOCK_TTL``
  seconds and the path is searched again around every blocked tile. The
  sidestep below is for minimap clicks only.
* **Creatures** (``walk_keys``): a blocked tile also opens a ``_SCAN_WINDOW``
  second window. In it, every arrow key is preceded by a health-bar scan of
  the game grid (:mod:`moonlight.perception.creatures`); creature tiles and
  one predicted tile per moving creature are avoided too. When they leave
  no way through, or the way around is over ``_DETOUR`` steps longer, the
  bot presses ``attack_combo`` (if the battle list has
  monsters and nothing is selected) instead of an arrow, and walks on as
  soon as a path opens.
* **Arrival**: within 1 tile (Chebyshev distance) of the waypoint on the
  same floor; ``node`` within 3 tiles, ``stand`` only on the exact tile.
  ``walk``/``node``/``stand``/``lure`` are then done (``lure`` walks with
  attacking off, see :meth:`Cavebot.luring`); ``rope``/``shovel``/``ladder`` click
  the waypoint's tile in the game grid (15x11 tiles, player at slot (7, 5))
  and wait for the expected floor change (rope/ladder: z - 1, shovel:
  z + 1), retrying every ``reclick_interval``. ``stairs`` walks onto the
  exact tile and is done when the floor changes, up or down.
* **label / goto**: positionless. ``label`` is a jump target; ``goto``
  jumps to the waypoint after the named label (route loops).
* **End of route**: a hunting route starts over at waypoint 0; a route
  with ``loop`` off (the refill legs) completes and the cavebot stops.
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

from moonlight.bot.state import Position, WorldState
from moonlight.decision.pathing import CostMap, next_step
from moonlight.hardware.clicker import ClickController
from moonlight.hardware.input import ClickSide, InputBackend
from moonlight.perception.creatures import find_creatures, predict
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
    "walk", "node", "stand", "lure", "rope", "shovel", "ladder", "stairs", "label", "goto"
)

#: Actions that need no position (executed immediately).
_JUMP_ACTIONS = ("label", "goto")

#: Floor change each tile action causes (0 = sky, 15 = deepest).
_FLOOR_DELTA = {"rope": -1, "ladder": -1, "shovel": +1}

#: Arrival distance in tiles; every other positional action uses 1.
_REACH = {"node": 3, "stand": 0, "stairs": 0}
# doc: docs/prs/keys-walking.md#arrow-steps
# doc: docs/prs/keys-walking.md#learned-blocks
#: Arrow key per one-tile step (y grows downward, up is north).
_ARROWS = {(0, -1): "up", (0, 1): "down", (-1, 0): "left", (1, 0): "right"}
#: Seconds without moving before an arrow key is pressed again.
_KEY_RETRY = 0.7
_BLOCK_TTL = 10.0
_SCAN_WINDOW = 5.0
_FIGHT_STALL = 5.0  # seconds the selected target's HP may stand still before the next one
_ATTACK_WAIT = 3.0  # seconds between attack presses (a press toggles/cycles the target)
_DETOUR = 8  # extra steps worth walking around creatures; longer -> fight through

#: Game grid layout (docs/ui_layout.md #10): 15x11 tiles, player at (7, 5).
GAME_GRID_COLS = 15
GAME_GRID_ROWS = 11
_PLAYER_SLOT = (7, 5)


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
            if wp.action == "goto" and wp.label not in labels:
                raise RouteError(f"waypoint {i}: goto target {wp.label!r} has no label")
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
        attack_combo: str | None = None,
        clock: Callable[[], float] | None = None,
        on_waypoint_reached: Callable[[int], None] | None = None,
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
            attack_combo: Pressed when creatures leave no way through.
            clock: Monotonic-clock override (tests).
            on_waypoint_reached: Called with the index of each reached
                positional waypoint.

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
        self._frames = frames
        self._attack_combo = attack_combo
        self._scan_until = float("-inf")
        self._seen: list[tuple[int, int]] = []  # creature tiles of the last scan
        self._creatures: set[tuple[int, int]] = set()  # seen + predicted
        self._fight: tuple[str, float] | None = None  # (name, HP) of the target it waits on
        self._fight_since = 0.0
        self._attack_at = float("-inf")
        self._clock = clock if clock is not None else time.monotonic
        self._callback = on_waypoint_reached

        #: Why the route stopped on its own ("stuck", "lost", "goto_loop"),
        #: or None.
        self.alarm: str | None = None
        self._active = False
        self._last_pos: Position | None = None
        self._last_move = 0.0
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
        self._clicker.cancel()

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
            self._last_pos = pos
            self._last_move = now
        elif now - self._last_move > self._stuck_timeout:
            self._halt("stuck")
            return
        if pos is None or not self._resolve_jumps():
            return

        wp = self.route.waypoints[self.route.current]
        if moved_aside and wp.action == "walk":
            logger.info("cavebot: got past a blocker, skipping waypoint %d", self.route.current)
            self._advance()
            return
        if self._expected_z is not None and pos.z == self._expected_z:
            self._advance()  # the rope/shovel/ladder action worked
            return
        if pos.z != wp.z and wp.action == "stairs":
            self._advance()  # stepping on the stairs changed the floor
            return
        if pos.z != wp.z:
            self._resync_floor(pos.z)
            return
        if max(abs(wp.x - pos.x), abs(wp.y - pos.y)) <= _REACH.get(wp.action, 1):
            if wp.action not in _FLOOR_DELTA:
                self._advance()
            else:
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
        elif self._last_move > self._last_cmd or now - self._last_cmd >= _KEY_RETRY:
            if self._last_move <= self._last_cmd and self._target is not None:
                logger.info("cavebot: tile %s,%s,%s blocked, walking around", *self._target)
                self._blocked[self._target] = now + _BLOCK_TTL
                self._scan_until = now + _SCAN_WINDOW
            self._step_toward(wp, pos, now)
            self._last_cmd = now

    # -- internals ----------------------------------------------------------

    def _reset_tracking(self) -> None:
        self._last_pos = None
        self._last_move = self._clock()
        self._last_cmd = float("-inf")
        self._expected_z = None
        self._sidesteps = 0

    def _halt(self, alarm: str) -> None:
        logger.warning("cavebot: route %r stopped: %s", self.route.name, alarm)
        self.alarm = alarm
        self._active = False
        self._clicker.cancel()

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
        wps = self.route.waypoints
        for _ in range(len(wps) + 1):
            if self.route.current >= len(wps):
                if self.route.loop:
                    self.route.current = 0
                    continue
                logger.info("cavebot: route %r complete", self.route.name)
                self._active = False
                return False
            wp = wps[self.route.current]
            if wp.action == "label":
                self.route.current += 1
            elif wp.action == "goto":
                self.route.current = next(
                    i + 1 for i, w in enumerate(wps) if w.action == "label" and w.label == wp.label
                )
            else:
                return True
        self._halt("goto_loop")
        return False

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
        """Press the arrow of the next step to *wp* around blocked tiles.

        Without map data every tile but the blocked ones counts as walkable.
        When creatures close the way, or walking around them costs more than
        ``_DETOUR`` extra steps, it attacks; with no path at all, it presses
        straight at *wp*.
        """
        self._blocked = {t: end for t, end in self._blocked.items() if end > now}
        cost = self._cost_map if self._cost_map is not None and not self._cost_map.empty else None
        self._scan(pos, now)

        def free(x: int, y: int, z: int) -> bool:  # a creature on a bumped tile can be killed
            return ((x, y, z) not in self._blocked or (x, y) in self._creatures) and (
                cost is None or cost.walkable(x, y, z)
            )

        def walkable(x: int, y: int, z: int) -> bool:
            return (x, y) not in self._creatures and free(x, y, z)

        start, goal = (pos.x, pos.y, pos.z), (wp.x, wp.y, wp.z)
        path = next_step(start, goal, walkable)
        if self._creatures:
            short = next_step(start, goal, free)
            if path is None or (short is not None and path[1] > short[1] + _DETOUR):
                self._target = None
                self._scan_until = now + _SCAN_WINDOW  # keep scanning while it fights
                self._attack(now)
                return
        if path is None:
            dx, dy = wp.x - pos.x, wp.y - pos.y
            step = ((dx > 0) - (dx < 0), 0) if abs(dx) >= abs(dy) else (0, (dy > 0) - (dy < 0))
        else:
            step = path[0]
        self._target = (pos.x + step[0], pos.y + step[1], pos.z)
        self._press_arrow(step)

    def _scan(self, pos: Position, now: float) -> None:
        """Refresh the creature tiles inside the scan window, else forget them."""
        frame = self._frames() if self._frames is not None and now < self._scan_until else None
        if frame is None:
            self._seen, self._creatures = [], set()
            return
        px, py = pos.x - _PLAYER_SLOT[0], pos.y - _PLAYER_SLOT[1]
        seen = [(px + c, py + r) for c, r in find_creatures(frame, self._game_region)]
        self._creatures = set(seen) | predict(seen, self._seen)
        self._seen = seen

    def _attack(self, now: float) -> None:
        """Creatures block the way: select one to fight (the loop fights it).

        While a target is selected it waits; its HP dropping counts as
        progress for the stuck timeout. HP standing still ``_FIGHT_STALL``
        seconds (out of reach) presses the key again for the next target.
        """
        if self._attack_combo is None or not self._state.monsters:
            return  # nothing to fight: wait, the stuck timeout decides
        if now - self._attack_at < _ATTACK_WAIT:
            return  # give the last press time to show a selection
        sel = self._state.selected_target
        if sel is not None:
            seen = (sel.name, sel.hp_percent)
            if seen != self._fight:
                if self._fight is not None and self._fight[0] == sel.name:
                    self._last_move = now  # its HP dropped: a fight is not "stuck"
                self._fight, self._fight_since = seen, now
                return
            if now - self._fight_since < _FIGHT_STALL:
                return
            logger.info("cavebot: target HP frozen, attacking the next one")
        else:
            logger.info("cavebot: creatures block the way, attacking")
        self._fight, self._fight_since, self._attack_at = None, now, now
        assert self._input is not None  # checked in __init__
        self._input.press_combo(self._attack_combo)

    def _press_arrow(self, step: tuple[int, int]) -> None:
        assert self._input is not None  # checked in __init__
        self._input.press_combo(_ARROWS[step])

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
        col = _PLAYER_SLOT[0] + wp.x - pos.x
        row = _PLAYER_SLOT[1] + wp.y - pos.y
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
