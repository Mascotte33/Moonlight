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

from moonlight.bot.state import Position, WorldState
from moonlight.hardware.clicker import ClickController
from moonlight.hardware.input import ClickSide, InputBackend
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
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RouteError(f"waypoint {index}: {key!r} must be a non-negative int")
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
        stuck_timeout: float = 5.0,
        reclick_interval: float = 1.0,
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
            input_backend: Presses the rope/shovel hotkeys; required when
                the route has rope or shovel waypoints.
            rope_hotkey: Combo bound to "use rope" in the client.
            shovel_hotkey: Combo bound to "use shovel" in the client.
            stuck_timeout: Seconds without movement before alarm "stuck".
            reclick_interval: Seconds standing still before a walk click or
                tile action is retried.
            clock: Monotonic-clock override (tests).
            on_waypoint_reached: Called with the index of each reached
                positional waypoint.

        Raises:
            ValueError: On bad timings, or rope/shovel waypoints without
                an input backend.
        """
        self._minimap_region: Rect = minimap_region
        if stuck_timeout <= 0 or reclick_interval <= 0:
            raise ValueError("stuck_timeout and reclick_interval must be > 0")
        needs_keys = any(wp.action in ("rope", "shovel") for wp in route.waypoints)
        if needs_keys and input_backend is None:
            raise ValueError("rope/shovel waypoints need an input_backend for hotkeys")
        self.route = route
        self._clicker = clicker
        self._state = state
        self._game_region = game_region
        self._input = input_backend
        self._hotkeys = {"rope": rope_hotkey, "shovel": shovel_hotkey}
        self._stuck_timeout = float(stuck_timeout)
        self._reclick_interval = float(reclick_interval)
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

    def resume(self) -> None:
        """MOVE (re)entered: restart the stuck timer and re-click at once."""
        self._last_move = self._clock()
        self._last_cmd = float("-inf")
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
        if pos is not None and pos != self._last_pos:
            self._last_pos = pos
            self._last_move = now
        elif now - self._last_move > self._stuck_timeout:
            self._halt("stuck")
            return
        if pos is None or not self._resolve_jumps():
            return

        wp = self.route.waypoints[self.route.current]
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
        self._retry_if_idle(now, lambda: self._walk_toward(wp, pos))

    # -- internals ----------------------------------------------------------

    def _reset_tracking(self) -> None:
        self._last_pos = None
        self._last_move = self._clock()
        self._last_cmd = float("-inf")
        self._expected_z = None

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
