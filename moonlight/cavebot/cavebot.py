"""Cavebot movement (T22).

Waypoint execution: walk/rope/shovel/ladder/stairs/label/goto/check/say/key, stuck detection.
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
  With ``walk_keys`` on, the bot holds one WSAD key per step instead:
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
* **Creatures** (``walk_keys``): every WSAD key is preceded by a
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
  :attr:`Cavebot.stuck_on_creatures`, stands still and right-clicks the
  first creature on the way; the loop then targets with every monster
  attacking (spells and area rules included), the top row only if that click
  selected nothing, and calls :meth:`Cavebot.on_target_killed` after each
  kill, so the cavebot looks for a way again over the tiles it bumped too.
  While a target is selected it stands still, whatever selected it, and
  walks on once nothing is (or while :attr:`Cavebot.targeting_paused`).
  Stuck after a bump with nothing to fight and no way over tiles not bumped
  yet: the waypoint is skipped, and the next ones in route order, until one
  on this floor has such a way; none: it stands still and retries the
  blocked tiles every ``_BLOCKED_RETRY`` seconds (a player in the only
  path), walking on once one is free.
* **Arrival**: within 1 tile (Chebyshev distance) of the waypoint on the
  same floor; ``node`` within 3 tiles, ``stand`` and ``rope`` only on the
  exact tile (a rope after standing still there ``_ROPE_SETTLE`` s).
  ``walk``/``node``/``stand``/``lure`` are then done (``lure`` walks with
  attacking off, see :meth:`Cavebot.luring`); ``rope``/``shovel``/``ladder`` click
  the waypoint's tile in the game grid (15x11 tiles, player at slot (7, 5))
  and wait for the expected floor change (rope: z - 1, shovel: z + 1,
  ladder: up or down, so a sewer grate is a ``ladder`` too), retrying
  every ``reclick_interval``. ``stairs`` walks onto the
  exact tile and is done when the floor changes, up or down. ``step``
  (docs/prs/steps.md#step-waypoint) walks onto the exact tile, presses its
  label's walk key once, never again, holds 0.5 s and is done, wherever that
  step led (a hole, a teleport, stairs). Arrow-key paths never cross the
  tiles of ``stairs`` and ``shovel`` waypoints, the tile a ``step`` presses
  into, or a tile that moved the player elsewhere when walked onto
  (docs/prs/steps.md#floor-change-tiles); such a tile is only ever the goal.
* **Tiles of label, goto, check and hand actions**: added in Edit route they
  have the tile the character stood on; the bot walks onto it first, then
  does the action. Old routes have them positionless (x, y, z 0): done
  where the bot stands.
* **label / goto**: ``label`` is a jump target; ``goto`` jumps to the
  waypoint after the named label (route loops).
* **check**: jumps like ``goto`` when refill is due (``supplies_low``: free
  cap or potions under their settings, or :attr:`Cavebot.refill_now` set,
  which the jump clears); otherwise it goes on to the next waypoint. The
  town trip is a section behind a label that ends in a ``goto`` back to
  the hunt.
* **say / key**: ``say`` presses Enter
  (opens the chat box), types its label, presses Enter (sends it, closes the
  box), so the chat stays off for WASD; ``key`` presses its label as a
  hotkey. One key per tick (a key the wire limiter drops is sent again),
  then the route holds 1 s so NPC answers and windows land.
* **wait**: holds the route for its label's seconds.
* **finddepo**: once its tile is in view it finds the depot lockers in the
  game view (:mod:`moonlight.cavebot.town`) and walks to the nearest free
  tile next to one (no creature or player, walkable). None free: it walks on
  to its tile and waits there, looking again every few seconds, with alarm
  ``"depot_taken"`` after 5 min.
* **deposit**: right-clicks the locker ``finddepo`` picked until the stash
  and the loot backpack (*backpack*) are on screen, then drags the backpack
  onto the stash. The locker window stays open.
* **bank**: on its tile, talks to the banker (chat off, Shift+F11 for the
  NPCs tab, chat on): ``hi``, ``deposit all``, ``yes`` with a short random
  pause after each, about 1 s, the three again, then the chat off. A chat
  button that will not switch is clicked again; alarm ``"bank"`` after 3 min.
* **trade**: on its tile, talks ``hi``, ``trade`` like ``bank`` and waits up
  to 3 s for the trade window (else talks again), clicks Buy, then buys each
  row of the route's buy list: search the name, pick the first result, drag
  the amount slider to the row's percent, Buy. A health or mana potion whose
  action-bar total did not go up is bought again. Alarm ``"trade"`` after 3 min.
* **script**: runs the user script named by its label
  (:mod:`moonlight.scripts.scripts`); the route holds until it ends.
* **End of route**: a hunting route starts over at waypoint 0; a route
  with ``loop`` off completes and the cavebot stops.
* **Blocked** (an NPC in the way): after ``sidestep_after`` seconds without
  moving, the bot clicks (or with ``walk_keys`` steps onto) the tile left of
  its walking direction, then
  ``sidestep_after`` later the tile right of it. Once it moves again after a
  sidestep, a ``walk`` waypoint is skipped; any other waypoint is retried.
* **Stuck**: position unchanged for ``stuck_timeout`` seconds ->
  :attr:`Cavebot.alarm` is set to ``"stuck"`` (the operator is alerted) but
  the route keeps trying; the alarm clears once it moves. The loop calls
  :meth:`Cavebot.resume` whenever MOVE is (re)entered, so time spent in a
  fight/loot/pause never counts as "stuck" (F-06).
* **Unexpected floor change** (z differs from what the route expects): the
  route re-syncs to the next waypoint on the new floor. With no waypoint on
  that floor it raises alarm ``"lost"`` and waits: a misread floor (right
  after a pause) comes back, and the route goes on from where it was.

Runtime failures never raise from :meth:`step` (an exception would kill
the loop); they stop the route and set :attr:`alarm`.
"""

from __future__ import annotations

import functools
import json
import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from moonlight.cavebot.pathing import CostMap, next_step
from moonlight.cavebot.town import STASH, TRADE, find_chat, find_lockers, find_picture, pick_locker
from moonlight.core.state import Position, WorldState
from moonlight.hardware.clicker import ClickController
from moonlight.hardware.input import ClickSide, InputBackend
from moonlight.perception.creatures import PLAYER_SLOT, find_creatures
from moonlight.perception.floor import MAX_FLOOR, MIN_FLOOR
from moonlight.perception.minimap import Rect

if TYPE_CHECKING:
    from moonlight.scripts.scripts import ScriptRunner

__all__ = [
    "ACTIONS",
    "GAME_GRID_COLS",
    "GAME_GRID_ROWS",
    "NO_TILE",
    "has_tile",
    "Cavebot",
    "Route",
    "RouteError",
    "RouteFileLoader",
    "Waypoint",
    "label_error",
]

logger = logging.getLogger(__name__)

#: Valid waypoint actions.
ACTIONS = (
    "walk", "node", "stand", "lure", "rope", "shovel", "ladder", "stairs", "label", "goto", "check",
    "say", "key", "drag", "script", "wait", "step", "finddepo",
    "deposit", "bank", "trade",
)  # fmt: skip

#: Actions that need no position (executed immediately).
_JUMP_ACTIONS = ("label", "goto", "check")
#: Actions done where the bot stands (one command per tick, then a hold).
_HAND_ACTIONS = ("say", "key", "drag", "script", "wait", "deposit", "bank", "trade")
#: Hand actions without a label.
_NO_LABEL = ("deposit", "bank", "trade")
#: Actions that may have no tile (x, y, z 0: old routes); added in Edit route they have one.
NO_TILE = _JUMP_ACTIONS + _HAND_ACTIONS


def has_tile(wp: Waypoint) -> bool:
    """True when the bot walks to *wp*'s tile before doing it."""
    return wp.action not in NO_TILE or (wp.x, wp.y, wp.z) != (0, 0, 0)


# doc: docs/prs/route-trust.md#walked-lines
def walked_tiles(waypoints: list[Waypoint], loop: bool) -> set[tuple[int, int, int]]:
    """Tiles on the straight line between neighbouring waypoints on one floor.

    A route is recorded by walking it, so these were walked; the client's
    cost map can still call one a wall (the Thais sewer row y 32218, 2026-11-01:
    the bot skipped 10 waypoints). A real wall is still found by bumping it.
    """
    tiles: set[tuple[int, int, int]] = set()
    spots = [(wp.x, wp.y, wp.z) for wp in waypoints if has_tile(wp)]
    pairs = list(zip(spots, spots[1:], strict=False))
    if loop and len(spots) > 2:
        pairs.append((spots[-1], spots[0]))
    for (x0, y0, z0), (x1, y1, z1) in pairs:
        if z0 != z1:
            continue
        n = max(abs(x1 - x0), abs(y1 - y0))
        for i in range(n + 1):
            tiles.add((round(x0 + (x1 - x0) * i / n), round(y0 + (y1 - y0) * i / n), z0))
    return tiles


#: Seconds the route holds after a hand action (a wait: its label).
_HAND_HOLD = {"say": 1.0, "key": 1.0, "drag": 1.0, "script": 0.0, "step": 0.5, "deposit": 1.0}

#: Floor change each tile action causes (0 = sky, 15 = deepest).
_FLOOR_DELTA = {"rope": -1, "ladder": -1, "shovel": +1}

#: Arrival distance in tiles; every other positional action uses 1.
_REACH = {"node": 3, "stand": 0, "stairs": 0, "step": 0, "rope": 0, **dict.fromkeys(NO_TILE, 0)}
# doc: docs/prs/steps.md#rope-settle
#: Seconds a rope waits standing on its tile before the rope is used.
_ROPE_SETTLE = 0.2
# doc: docs/prs/steps.md#floor-change-tiles
#: Waypoint actions whose tile moves the player when walked onto.
_DANGER_ACTIONS = ("stairs", "shovel")
#: Tiles moved in one position change that count as a teleport, not a step.
_JUMP_TILES = 2
# doc: docs/prs/keys-walking.md#arrow-steps
# doc: docs/prs/keys-walking.md#learned-blocks
#: Walk key per one-tile step (y grows downward, w is north); q/e/z/c walk diagonally.
WALK_KEYS = {
    (0, -1): "w", (0, 1): "s", (-1, 0): "a", (1, 0): "d",
    (-1, -1): "q", (1, -1): "e", (-1, 1): "z", (1, 1): "c",
}  # fmt: skip
#: Seconds without moving before an WSAD key is pressed again.
_KEY_RETRY = 0.7
#: Outlasts bumping all 8 neighbours (2 presses each); a creature walking off frees its tile.
_BLOCK_TTL = 12.0
#: With no way past the blocked tiles (a player on the only path), they are
#: tried again this often.
_BLOCKED_RETRY = 2.0
_STUCK_WINDOW = 5.0
#: Seconds the battle-list pick waits for a right-click on the creature in the way.
_CREATURE_IN_THE_WAY_CLICK_WAIT = 1.5
#: The game view scrolls a step after the position changed: a scan then sees creatures a tile off.
_VIEW_SETTLE = 0.5
_DETOUR = 8  # extra steps worth walking around creatures; longer -> fight through
# doc: docs/prs/depot.md#npc-pacing
#: Seconds paused after each NPC line (owner's ranges: the NPC needs time to react).
_NPC_PAUSE = {"hi": (0.4, 0.7), "deposit all": (0.7, 1.0), "trade": (0.7, 1.0), "yes": (0.4, 0.6)}
#: The owner's hotkey that opens or selects the NPCs chat tab (only with the chat off).
_NPC_TAB = "shift+f11"
#: Seconds between the two rounds of bank lines.
_BANK_ROUNDS_PAUSE = 1.0
#: Seconds a chat button click gets to show before it is clicked again.
_CHAT_CLICK_WAIT = 0.5
# doc: docs/prs/depot.md#retry-alarm
#: Seconds an NPC talk keeps failing before its alarm.
_TALK_ALARM = 180.0
#: Seconds a trade waits for its window before talking again.
_TRADE_WAIT = 3.0
# doc: docs/prs/depot.md#finddepo-waypoint
#: Seconds between looks for a free locker, and before the alarm when all are taken.
_DEPOT_RELOOK = 3.0
_DEPOT_ALARM = 300.0

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
    # doc: docs/prs/depot.md#trade-waypoint
    #: What a ``trade`` buys, in order: (item name, slider percent 0..100).
    buy: list[tuple[str, int]] = field(default_factory=list)
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
        if not any(has_tile(wp) for wp in waypoints):
            raise RouteError("route needs at least one positional waypoint")
        labels = [wp.label for wp in waypoints if wp.action == "label"]
        if len(labels) != len(set(labels)):
            raise RouteError("duplicate label names")
        for i, wp in enumerate(waypoints):
            if wp.action in ("goto", "check") and wp.label not in labels:
                raise RouteError(f"waypoint {i}: {wp.action} target {wp.label!r} has no label")
        return Route(name=name, description=description, waypoints=waypoints,
                     buy=RouteFileLoader._buy(raw.get("buy", [])))  # fmt: skip

    @staticmethod
    def _buy(items: object) -> list[tuple[str, int]]:
        if not isinstance(items, list):
            raise RouteError("route 'buy' must be a list")
        rows = []
        for i, row in enumerate(items):
            item = row.get("item") if isinstance(row, dict) else None
            percent = row.get("percent") if isinstance(row, dict) else None
            if not isinstance(item, str) or not item.strip():
                raise RouteError(f"buy row {i}: 'item' must be a non-empty string")
            if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
                raise RouteError(f"buy row {i}: 'percent' must be 0..100")
            rows.append((item.strip(), percent))
        return rows

    @staticmethod
    def _waypoint(index: int, item: object) -> Waypoint:
        if not isinstance(item, dict):
            raise RouteError(f"waypoint {index}: must be an object")
        action = item.get("action", "walk")
        if action not in ACTIONS:
            raise RouteError(f"waypoint {index}: action must be one of {ACTIONS}, got {action!r}")
        label = item.get("label", "")
        if action in NO_TILE:
            if not isinstance(label, str) or (not label and action not in _NO_LABEL):
                raise RouteError(f"waypoint {index}: {action} needs a non-empty 'label'")
            if bad := label_error(action, label):
                raise RouteError(f"waypoint {index}: {bad}")
            if "x" not in item:
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
        if action not in (*NO_TILE, "step"):
            label = ""
        elif action == "step" and label not in WALK_KEYS.values():
            keys = list(WALK_KEYS.values())
            raise RouteError(f"waypoint {index}: step label must be one of {keys}")
        return Waypoint(coords[0], coords[1], coords[2], action, label)


def label_error(action: str, label: str) -> str | None:
    """Why *label* cannot be the label of a hand action (``say``, ``wait``...), or None."""
    if action == "say" and not re.fullmatch(r"[A-Za-z0-9 ]+", label):
        return "say text is letters, digits and spaces"
    if action == "drag" and not re.fullmatch(r"\d+ \d+ \d+ \d+", label):
        return "drag label is four numbers: x1 y1 x2 y2"
    if action == "key" and any(ch.isspace() for ch in label):
        return f"key {label!r} has a space"
    if action == "wait" and not re.fullmatch(r"\d+(\.\d+)?", label):
        return "wait label is seconds"
    if action == "script" and not re.fullmatch(r"\w+", label):
        return "script label is a file name in scripts/ without .py"
    return None


def say_keys(text: str) -> list[str]:
    """Enter opens the chat box, *text* is typed, Enter sends it and closes the box."""
    return ["enter"] + [ch if ch != " " else "space" for ch in text.lower()] + ["enter"]


#: An NPC talk step (:func:`talk_steps`, :func:`trade_steps`).
TalkStep = str | bool | float | tuple[str, int, int]
# Trade window spots from the centre of ``npc_trade.png`` (2026-10-30 grab).
_BUY_SWITCH = (117, -5)
_SEARCH, _SEARCH_CLEAR = (38, 264), (129, 265)
_FIRST_ROW, _BUY = (53, 44), (116, 325)
#: The amount slider's thumb at amount 1, and how far right it goes.
_SLIDER_MIN, _SLIDER_SPAN = (-20, 283), 93


# doc: docs/prs/depot.md#npc-pacing
def talk_steps(*rounds: tuple[str, ...]) -> list[TalkStep]:
    """An NPC talk: True / False turns the chat on / off, a float pauses
    that many seconds, a string is a key. Chat off, Shift+F11 (NPCs tab),
    chat on, each line typed with Enter and a random pause, chat off."""
    steps: list[TalkStep] = [False, _NPC_TAB, True]
    for i, lines in enumerate(rounds):
        if i:
            steps.append(_BANK_ROUNDS_PAUSE)
        for line in lines:
            steps += say_keys(line)[1:]  # the chat is on: no Enter to open it
            steps.append(random.uniform(*_NPC_PAUSE[line]))
    return [*steps, False]


# doc: docs/prs/depot.md#trade-waypoint
def trade_steps(buy: list[tuple[str, int]]) -> list[TalkStep]:
    """The ``trade`` talk, then the buy list in the trade window. Tuples are
    ``(op, a, b)``: ``win`` waits for the window, ``click`` / ``to`` click /
    move the mouse at (a, b) from the window's ``npc_trade.png``, ``down`` /
    ``up`` the left button, ``count`` reads the potions, ``health`` / ``mana``
    goes back to step a when that total did not go up."""
    steps: list[TalkStep] = [*talk_steps(("hi", "trade")), ("win", 0, 0), ("click", *_BUY_SWITCH)]
    for item, percent in buy:
        row = len(steps)
        steps += [("count", 0, 0), ("click", *_SEARCH_CLEAR), 0.3, ("click", *_SEARCH), 0.3]
        steps += say_keys(item)[1:-1]  # the search box has the keys: no Enter
        steps += [0.8, ("click", *_FIRST_ROW), 0.3]
        if percent:
            x, y = _SLIDER_MIN
            right = x + round(percent * _SLIDER_SPAN / 100)
            steps += [("to", x, y), ("down", 0, 0), ("to", right, y), ("up", 0, 0), 0.3]
        steps += [("click", *_BUY), 1.0]
        steps += [(kind, row, 0) for kind in ("health", "mana") if kind in item.lower()]
    return [*steps, False]


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
        scripts: ScriptRunner | None = None,
        supplies_low: Callable[[], bool] | None = None,
        backpack: Path | None = None,
        potion_counts: Callable[[], dict[str, int | None]] | None = None,
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
            walk_keys: Walk with the WSAD keys instead of minimap clicks.
            cost_map: Walkable tiles for *walk_keys* path finding; without
                it the bot steps straight at the waypoint.
            frames: The current screenshot, for the creature scan; without
                it creatures are found only by bumping into them.
            clock: Monotonic-clock override (tests).
            on_waypoint_reached: Called with the index of each reached
                positional waypoint.
            scripts: Starts the user scripts of ``script`` waypoints.
            supplies_low: True when free cap or potions are under their
                Refill settings; a ``check`` then jumps too.
            backpack: The loot backpack picture a ``deposit`` drags onto the stash.
            potion_counts: The action-bar health / mana totals a ``trade`` checks.

        Raises:
            ValueError: On bad timings, or rope/shovel waypoints or
                *walk_keys* without an input backend.
        """
        self._minimap_region: Rect = minimap_region
        if stuck_timeout <= 0 or reclick_interval <= 0:
            raise ValueError("stuck_timeout and reclick_interval must be > 0")
        keyed = ("rope", "shovel", "say", "key", "drag", "step", "deposit", "bank", "trade")
        needs_keys = any(wp.action in keyed for wp in route.waypoints)
        if needs_keys and input_backend is None:
            raise ValueError("rope/shovel/say/key/drag/step waypoints need an input_backend")
        hand_drags = ("drag", "deposit", "trade")
        if any(wp.action in hand_drags for wp in route.waypoints) and not clicker.absolute:
            raise ValueError("drag, deposit and trade waypoints need the absolute clicker")
        if walk_keys and input_backend is None:
            raise ValueError("walk_keys needs an input_backend for the WSAD keys")
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
        self._walked: tuple[int, set[tuple[int, int, int]]] = (0, set())
        self._blocked: dict[tuple[int, int, int], float] = {}  # tile -> expiry
        self._blocked_wait = False  # no way past the blocked tiles: retrying
        #: Tiles a walk key stepped onto that moved the player elsewhere (a hole, a teleport).
        self._jumped: set[tuple[int, int, int]] = set()
        self._target: tuple[int, int, int] | None = None
        #: (x, y) tiles of the walk planned last on the player's floor, for the
        #: "What the bot sees" view (docs/prs/extras.md#seen-path).
        self.planned_path: list[tuple[int, int]] = []
        # doc: docs/prs/tunnel-fights.md#creature-in-the-way
        self.creature_in_the_way_clicked_until = float("-inf")
        #: The target tile whose key was pressed a second time after not moving.
        self._retried_target: tuple[int, int, int] | None = None
        #: The walk key held down, kept between steps so the client walks on.
        self._held_walk_key: str | None = None
        self._frames = frames
        #: Creatures leave no way through: the loop targets them (cleared after each kill).
        self.stuck_on_creatures = False
        #: Set by the loop while targeting is paused: walk on past a selected target.
        self.targeting_paused = False
        self._stuck_allowed_until = float("-inf")
        self._creatures: set[tuple[int, int]] = set()  # tiles the last scan saw
        self._previous_pos: Position | None = None  # where the view still is right after a step
        #: Bumped tiles a scan saw a creature on: free again once it walks off.
        self._bumped_into_creature: set[tuple[int, int, int]] = set()
        self._clock = clock if clock is not None else time.monotonic
        self._callback = on_waypoint_reached
        self._supplies_low = supplies_low
        #: Refill now: the next ``check`` jumps to town (cleared by the jump).
        self.refill_now = False
        self._scripts = scripts
        self._sends_done = 0  # of the current hand action
        self._hand_done_at: float | None = None
        #: The locker the last ``finddepo`` picked (x, y, z), for ``deposit``.
        self.depot_locker: tuple[int, int, int] | None = None
        self._depot_stand: Waypoint | None = None  # the tile next to it
        self._depot_looked = float("-inf")
        self._depot_wait_since: float | None = None
        self._backpack = backpack
        #: ``deposit``: (backpack, stash) frame pixels once both are on screen.
        self._deposit_drag: tuple[tuple[int, int], tuple[int, int]] | None = None
        self._locker_clicked = float("-inf")
        #: The NPC talk under way (talk_steps), when it started, and no step before.
        self._talk: list[TalkStep] | None = None
        self._talk_since = 0.0
        self._talk_wait = float("-inf")
        #: ``trade``: the window's anchor, since when it is awaited, the potions before a row.
        self._trade_at: tuple[int, int] | None = None
        self._trade_wait_since: float | None = None
        self._potions_before: dict[str, int | None] = {}
        self._potion_counts = potion_counts

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

    def goto(self, label: str) -> None:
        """Walk on from label *label* (a script's ``bot.goto``); an unknown one is logged."""
        wps = self.route.waypoints
        at = next((i for i, w in enumerate(wps) if w.action == "label" and w.label == label), None)
        if at is None:
            logger.warning("cavebot: goto %r: no such label", label)
        else:
            self.go_to(at)

    def resume(self) -> None:
        """MOVE (re)entered: restart the stuck timer and re-click at once."""
        self._last_move = self._clock()
        self._last_cmd = float("-inf")
        self._sidesteps = 0
        self._clicker.cancel()  # a click queued before leaving MOVE is stale
        # A fight chased the character elsewhere: that is no jump to learn.
        self._last_pos = self._previous_pos = None

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
            self._learn_jump(pos)
            if self._blocked_wait:
                logger.info("cavebot: the way is free again")
                self._blocked_wait = False
            if self.alarm == "stuck":
                logger.info("cavebot: moving again")
                self.alarm = None
        elif now - self._last_move > self._stuck_timeout and self.alarm != "stuck":
            seen = "unknown" if pos is None else f"{pos.x},{pos.y},{pos.z}"
            logger.warning(
                "cavebot: [stuck: no-move] route %r: position %s unchanged for %g s, still trying",
                self.route.name, seen, self._stuck_timeout,
            )  # fmt: skip
            self.alarm = "stuck"  # alert the operator, keep trying
        if pos is None or not self._resolve_jumps():
            self.release_walk_key()
            return

        wp = self.route.waypoints[self.route.current]
        if wp.action == "finddepo":
            stand = self._find_depot(wp, pos, now)
            if stand is None:
                self.release_walk_key()
                return
            wp = stand
        if (wp.action in _HAND_ACTIONS and not has_tile(wp)) or (
            wp.action in (*_HAND_ACTIONS, "step") and self._stepped(wp, pos)
        ):
            self.release_walk_key()
            self._hand_action(wp, now)
            return
        if moved_aside and wp.action == "walk":
            logger.info(
                "cavebot: [stuck: sidestep] got past a blocker, skipping waypoint %d",
                self.route.current,
            )
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
        if self.alarm == "lost":
            logger.info("cavebot: back on floor %d, route goes on", pos.z)
            self.alarm = None
        if max(abs(wp.x - pos.x), abs(wp.y - pos.y)) <= _REACH.get(wp.action, 1):
            if wp.action not in _FLOOR_DELTA:
                self._advance()
            else:
                self.release_walk_key()
                if wp.action == "rope" and now - self._moved_at < _ROPE_SETTLE:
                    return  # stop on the rope spot first
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
        elif self._clicker.busy:
            self._clicker.step()  # the chat button
        elif (
            self._state.selected_target is not None
            and not self.luring()
            and not self.targeting_paused
        ):
            self._target = None  # stand still while a target is selected
            self.release_walk_key()
            self._stuck_allowed_until = now + _STUCK_WINDOW  # the fight may have closed the way
        elif self._creatures_moved(pos, now):
            planned_target = self._target
            if planned_target is not None and planned_target[:2] in self._creatures:
                self._stuck_allowed_until = now + _STUCK_WINDOW  # like a bump: it may close the way
            self._step_toward(wp, pos, now)
            if self._target != planned_target:  # same step: keep the bump timer running
                logger.info("cavebot: creatures moved, walking another way")
                self._retried_target = None
                self._last_cmd = now
        elif self._last_move > self._last_cmd or now - self._last_cmd >= _KEY_RETRY:
            target = self._target if self._last_move <= self._last_cmd else None
            step = None if target is None else (target[0] - pos.x, target[1] - pos.y)
            if step is not None and target != self._retried_target and step in WALK_KEYS:
                self._retried_target = target  # a lag looks like a block: press once more
                self._press_walk_key(step, again=True)
            elif target is not None and self._chat_turned_off():
                self._retried_target = None  # the key typed into the chat: press it again
            else:
                if target is not None:
                    logger.info(
                        "cavebot: [stuck: blocked-tile] tile %s,%s,%s blocked, walking around",
                        *target,
                    )
                    self._blocked[target] = now + _BLOCK_TTL
                    self._stuck_allowed_until = now + _STUCK_WINDOW
                self._retried_target = None
                self._step_toward(wp, pos, now)
            self._last_cmd = now

    def on_target_killed(self) -> None:
        """A kill: look for a way again, over the tiles bumped so far too.

        The bumped tile is most often the dead creature's; kept, it closes a
        one-tile corridor and the waypoint is skipped instead of walked to.
        """
        self.stuck_on_creatures = False
        self._blocked.clear()  # ponytail: a wall is bumped once more, cheap
        self._bumped_into_creature.clear()

    # -- internals ----------------------------------------------------------

    # doc: docs/prs/depot.md#chat-toggle
    def _chat_turned_off(self) -> bool:
        """A walk key that did not move: with the chat on it typed a letter,
        so click the chat button off. True when it was on."""
        frame = self._frames() if self._frames is not None else None
        chat = None if frame is None else find_chat(frame)
        if chat is None or not chat[0]:
            return False
        logger.info(
            "cavebot: [stuck: chat-on] the chat is on, the walk keys type into it: turning it off"
        )
        self.release_walk_key()
        self._clicker.cancel()
        self._clicker.request(chat[1])
        return True

    def _reset_tracking(self) -> None:
        self._last_pos = None
        self._last_move = self._clock()
        self._last_cmd = float("-inf")
        self._expected_z = None
        self._sidesteps = 0
        self.stuck_on_creatures = False
        self._sends_done, self._hand_done_at = 0, None
        self._talk = None
        self._blocked_wait = False

    def _halt(self, alarm: str) -> None:
        logger.warning("cavebot: [stuck: %s] route %r stopped", alarm, self.route.name)
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
            if has_tile(wp) and wp.z == pos.z
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
            if has_tile(wp):
                return index
            elif wp.action == "label":
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
        if self.refill_now:
            logger.info("check: Refill now | refill")
            return True
        return self._supplies_low is not None and self._supplies_low()

    # doc: docs/prs/refill.md#drag-waypoint
    def _hand_sends(self, wp: Waypoint) -> list[Callable[[], object]]:
        """The wire commands of a ``say`` / ``key`` / ``drag``, in order."""
        if wp.action == "wait":
            return []
        if wp.action == "script":
            scripts = self._scripts
            return [] if scripts is None else [lambda: scripts.start(wp.label)]
        send = self._input
        assert send is not None  # __init__: hand actions need it
        if wp.action in ("drag", "deposit"):
            if wp.action == "deposit":
                assert self._deposit_drag is not None  # _hand_action: found first
                (x1, y1), (x2, y2) = self._deposit_drag
            else:
                x1, y1, x2, y2 = (int(v) for v in wp.label.split())
            a, b = self._clicker.to_wire((x1, y1)), self._clicker.to_wire((x2, y2))
            return [
                lambda: send.mouse_to(*a),
                lambda: send.mouse_down(ClickSide.LEFT),
                lambda: send.mouse_to(*b),
                lambda: send.mouse_up(ClickSide.LEFT),
            ]
        keys = [wp.label] if wp.action in ("key", "step") else say_keys(wp.label)
        return [functools.partial(send.press_combo, k) for k in keys]

    # doc: docs/prs/refill.md#say-key-waypoints
    def _hand_action(self, wp: Waypoint, now: float) -> None:
        """Send the next command of a hand action, then hold before moving on."""
        self._last_move = now  # standing still here is not being stuck
        if wp.action == "deposit" and self._deposit_drag is None:
            if self._clicker.busy:
                self._clicker.step()  # the right-click on the locker
            else:
                self._open_locker(now)
            return
        if wp.action in ("bank", "trade"):
            self._talk_step(wp, now)
            return
        if self._hand_done_at is None:
            sends = self._hand_sends(wp)
            if self._sends_done < len(sends):
                dropped = getattr(self._input, "dropped", 0)
                sends[self._sends_done]()
                if getattr(self._input, "dropped", 0) == dropped:
                    self._sends_done += 1
                return
            self._hand_done_at = now
        hold = float(wp.label) if wp.action == "wait" else _HAND_HOLD[wp.action]
        if now - self._hand_done_at >= hold:
            self._sends_done, self._hand_done_at = 0, None
            self._advance()

    # doc: docs/prs/depot.md#chat-toggle
    # doc: docs/prs/depot.md#bank-deposit
    def _talk_step(self, wp: Waypoint, now: float) -> None:
        """One step of an NPC talk (:func:`talk_steps`) per tick, then the route moves on."""
        if self._talk is None:
            self._talk = (
                trade_steps(self.route.buy)
                if wp.action == "trade"
                else talk_steps(("hi", "deposit all", "yes"), ("hi", "deposit all", "yes"))
            )
            self._talk_since, self._talk_wait = now, float("-inf")
        if now - self._talk_since >= _TALK_ALARM and self.alarm != wp.action:
            logger.warning("cavebot: %s: no success for 3 min, still trying", wp.action)
            self.alarm = wp.action
        if self._clicker.busy:
            self._clicker.step()  # the chat button
            return
        if now < self._talk_wait:
            return
        if self._sends_done == len(self._talk):
            if self.alarm == wp.action:
                self.alarm = None
            self._advance()
            return
        step = self._talk[self._sends_done]
        if isinstance(step, bool):  # the chat on (True) or off
            frame = self._frames() if self._frames is not None else None
            chat = None if frame is None else find_chat(frame)
            if chat is None:
                return  # not seen: look again
            if chat[0] == step:
                self._sends_done += 1
            else:
                self._clicker.request(chat[1])
                self._talk_wait = now + _CHAT_CLICK_WAIT
        elif isinstance(step, float):
            self._talk_wait = now + step
            self._sends_done += 1
        elif isinstance(step, tuple):
            self._trade_op(*step, now)
        else:
            assert self._input is not None  # __init__: bank needs it
            dropped = getattr(self._input, "dropped", 0)
            self._input.press_combo(step)
            if getattr(self._input, "dropped", 0) == dropped:
                self._sends_done += 1

    # doc: docs/prs/depot.md#trade-waypoint
    def _trade_op(self, op: str, a: int, b: int, now: float) -> None:
        """One tuple step of :func:`trade_steps`."""
        if op == "win":
            frame = self._frames() if self._frames is not None else None
            self._trade_at = None if frame is None else find_picture(frame, TRADE)
            if self._trade_at is not None:
                self._trade_wait_since = None
                self._sends_done += 1
            elif self._trade_wait_since is None:
                self._trade_wait_since = now
            elif now - self._trade_wait_since >= _TRADE_WAIT:
                logger.info("cavebot: trade: no trade window, talking again")
                self._trade_wait_since, self._sends_done = None, 0
            return
        if op in ("count", "health", "mana"):
            counts = self._potion_counts() if self._potion_counts is not None else {}
            if op == "count":
                self._potions_before = counts
            else:
                before, after = self._potions_before.get(op), counts.get(op)
                if before is not None and after is not None and after <= before:
                    logger.info("cavebot: trade: %s potions did not go up, buying again", op)
                    self._sends_done = a
                    return
            self._sends_done += 1
            return
        send = self._input
        assert self._trade_at is not None and send is not None  # "win" came first
        point = (self._trade_at[0] + a, self._trade_at[1] + b)
        if op == "click":
            self._clicker.request(point)
            self._sends_done += 1
            return
        dropped = getattr(send, "dropped", 0)
        if op == "to":
            send.mouse_to(*self._clicker.to_wire(point))
        elif op == "down":
            send.mouse_down(ClickSide.LEFT)
        else:
            send.mouse_up(ClickSide.LEFT)
        if getattr(send, "dropped", 0) == dropped:
            self._sends_done += 1

    # doc: docs/prs/steps.md#step-waypoint
    def _stepped(self, wp: Waypoint, pos: Position) -> bool:
        """True once a ``step`` or hand action is on its tile: from then on it runs."""
        started = self._sends_done > 0 or self._hand_done_at is not None or self._talk is not None
        if not started and (pos.x, pos.y, pos.z) == (wp.x, wp.y, wp.z):
            self._clicker.cancel()  # no walk click after the key
            return True
        return started

    # doc: docs/prs/steps.md#floor-change-tiles
    def _learn_jump(self, pos: Position) -> None:
        """Remember the key's target tile when walking onto it moved the player elsewhere."""
        old, target = self._previous_pos, self._target
        wp = self.route.waypoints[self.route.current]
        if old is None or target is None or wp.action in ("step", *_DANGER_ACTIONS, *_FLOOR_DELTA):
            return
        if pos.z != old.z or max(abs(pos.x - old.x), abs(pos.y - old.y)) > _JUMP_TILES:
            logger.info("cavebot: tile %s,%s,%s moved the player, avoiding it", *target)
            self._jumped.add(target)

    def _danger_tiles(self) -> set[tuple[int, int, int]]:
        """Tiles that change the floor or teleport: stairs, holes, step targets, learned jumps."""
        tiles = set(self._jumped)
        for wp in self.route.waypoints:
            if wp.action in _DANGER_ACTIONS:
                tiles.add((wp.x, wp.y, wp.z))
            elif wp.action == "step":
                dx, dy = next(d for d, key in WALK_KEYS.items() if key == wp.label)
                tiles.add((wp.x + dx, wp.y + dy, wp.z))
        return tiles

    # doc: docs/prs/walking.md#skip-unreachable
    def _skip_unreachable(self, pos: Position, walkable: Callable[[int, int, int], bool]) -> None:
        """Move on to the next waypoint with a path over *walkable*; none: alarm "stuck"."""
        start, index = (pos.x, pos.y, pos.z), self.route.current
        for _ in range(len(self.route.waypoints)):
            following = self._positional_from(index + 1)
            if following is None or following == self.route.current:
                break
            index = following
            wp = self.route.waypoints[index]
            goal = (wp.x, wp.y, wp.z)
            if wp.z == pos.z and (goal == start or next_step(start, goal, walkable) is not None):
                logger.info(
                    "cavebot: [stuck: unreachable] waypoint %d unreachable, skipping to %d",
                    self.route.current,
                    index,
                )
                self.route.current = index
                self._target = self._retried_target = None
                return
        if not self._active:  # a goto loop already halted it
            return
        if not self._blocked_wait:
            logger.info(
                "cavebot: [stuck: no-way-past] no way past the blocked tiles, retry every %g s",
                _BLOCKED_RETRY,
            )
            self._blocked_wait = True
        # The blocked tiles expire soon: the next try walks into them again.
        retry = self._clock() + _BLOCKED_RETRY
        self._blocked = {t: min(end, retry) for t, end in self._blocked.items()}

    def _advance(self) -> None:
        self.on_waypoint_reached(self.route.current)
        wp = self.route.waypoints[self.route.current]
        if wp.action == "goto" or (wp.action == "check" and self._refill_due()):
            if wp.action == "check":
                logger.info("cavebot: refill due, jump to %r", wp.label)
                self.refill_now = False
            wps = self.route.waypoints
            self.route.current = next(
                i for i, w in enumerate(wps) if w.action == "label" and w.label == wp.label
            )
        self.route.current += 1
        self._depot_stand, self._depot_wait_since = None, None
        self._deposit_drag, self._locker_clicked = None, float("-inf")
        self._talk, self._sends_done = None, 0
        self._trade_at = self._trade_wait_since = None
        self._expected_z = None
        self._last_cmd = float("-inf")
        self._clicker.cancel()  # the pending click targeted the old waypoint
        self._resolve_jumps()

    # doc: docs/prs/depot.md#finddepo-waypoint
    def _find_depot(self, wp: Waypoint, pos: Position, now: float) -> Waypoint | None:
        """Where ``finddepo`` walks: toward its own tile, looking for lockers
        once that tile is in view, then the tile next to the picked locker;
        None while it waits on its tile for a free locker."""
        if self._depot_stand is not None:
            return self._depot_stand
        dx, dy = wp.x - pos.x, wp.y - pos.y
        if pos.z != wp.z or abs(dx) > PLAYER_SLOT[0] or abs(dy) > PLAYER_SLOT[1]:
            return wp  # walk to where the lockers are in view
        on_tile = dx == dy == 0
        if on_tile:
            self._last_move = now  # waiting here is not being stuck
        if now - self._depot_looked < _DEPOT_RELOOK:
            return None if on_tile else wp
        self._depot_looked = now
        frame = self._frames() if self._frames is not None else None
        picked = None
        if frame is not None:
            px, py = pos.x - PLAYER_SLOT[0], pos.y - PLAYER_SLOT[1]
            lockers = {(px + c, py + r) for c, r in find_lockers(frame, self._game_region)}
            taken = {(px + c, py + r) for c, r in find_creatures(frame, self._game_region)}
            cost = (
                self._cost_map if self._cost_map is not None and not self._cost_map.empty else None
            )

            def free(x: int, y: int) -> bool:
                if (x, y) in taken:
                    return False
                if cost is None:  # ponytail: no cost map, only the tile south of a locker
                    return (x, y - 1) in lockers
                return cost.walkable(x, y, pos.z)

            picked = pick_locker(lockers, (pos.x, pos.y), free)
        if picked is None:
            if not on_tile:
                return wp  # lockers taken or not in view yet: keep going
            if self._depot_wait_since is None:
                logger.info("cavebot: no free depot locker, waiting")
                self._depot_wait_since = now
            elif now - self._depot_wait_since >= _DEPOT_ALARM and self.alarm != "depot_taken":
                logger.warning("cavebot: every depot locker taken for 5 min, still waiting")
                self.alarm = "depot_taken"
            return None
        (lx, ly), (sx, sy) = picked
        logger.info("cavebot: depot locker %s,%s, standing at %s,%s", lx, ly, sx, sy)
        if self.alarm == "depot_taken":
            self.alarm = None
        self.depot_locker = (lx, ly, pos.z)
        self._depot_stand = Waypoint(sx, sy, pos.z, "stand")
        return self._depot_stand

    # doc: docs/prs/depot.md#deposit-waypoint
    def _open_locker(self, now: float) -> None:
        """``deposit``: right-click the picked locker until the stash and the
        loot backpack are on screen; still missing 3 s later, alarm ``"deposit"``."""
        frame = self._frames() if self._frames is not None else None
        bag_file = self._backpack
        stash = None if frame is None else find_picture(frame, STASH)
        bag = None if frame is None or bag_file is None else find_picture(frame, bag_file)
        if stash is not None and bag is not None:
            if self.alarm == "deposit":
                self.alarm = None
            self._deposit_drag = (bag, stash)
            return
        if now - self._locker_clicked < _DEPOT_RELOOK:
            return
        if self._locker_clicked > float("-inf") and self.alarm != "deposit":
            found = (("stash", stash), ("backpack", bag))
            missing = " and ".join(name for name, at in found if at is None)
            logger.warning("cavebot: deposit: no %s on screen, still trying", missing)
            self.alarm = "deposit"
        self._locker_clicked = now
        locker, pos = self.depot_locker, self._last_pos
        if locker is not None and pos is not None and locker[2] == pos.z:
            self._clicker.request(self._tile_pixel(locker[:2], pos), side=ClickSide.RIGHT)

    def _resync_floor(self, z: int) -> None:
        """Jump to the next positional waypoint on floor *z* (wrapping)."""
        wps = self.route.waypoints
        n = len(wps)
        for k in range(n):
            i = (self.route.current + k) % n
            if has_tile(wps[i]) and wps[i].z == z:
                logger.info("cavebot: floor changed to %d, resync to waypoint %d", z, i)
                self.route.current = i
                self._expected_z = None
                self._last_cmd = float("-inf")
                self._clicker.cancel()
                return
        # doc: docs/prs/position-lost.md#wait-on-an-unknown-floor
        # The route used to stop here for good: a floor misread for one tick
        # after a pause (bot.log 2026-10-31 22:44:01) ended the hunt.
        if self.alarm != "lost":
            logger.warning("cavebot: [stuck: lost] no waypoint on floor %d, waiting", z)
            self.alarm = "lost"

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

    def _map_walkable(self, x: int, y: int, z: int) -> bool:
        """The cost map's word, or a tile the route walked between two waypoints."""
        key = hash((id(self.route.waypoints), len(self.route.waypoints), self.route.loop))
        if self._walked[0] != key:
            self._walked = (key, walked_tiles(self.route.waypoints, self.route.loop))
        assert self._cost_map is not None
        return self._cost_map.walkable(x, y, z) or (x, y, z) in self._walked[1]

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
        danger = self._danger_tiles()  # never walked over, only onto as the goal

        def free(x: int, y: int, z: int) -> bool:  # a creature on a bumped tile can be killed
            return (
                ((x, y, z) not in self._blocked or (x, y) in self._creatures)
                and (x, y, z) not in danger
                and (cost is None or self._map_walkable(x, y, z))
            )

        def walkable(x: int, y: int, z: int) -> bool:
            return (x, y) not in self._creatures and free(x, y, z)

        def not_bumped(x: int, y: int, z: int) -> bool:
            return (
                (x, y, z) not in self._blocked
                and (x, y, z) not in danger
                and (cost is None or self._map_walkable(x, y, z))
            )

        start, goal = (pos.x, pos.y, pos.z), (wp.x, wp.y, wp.z)
        path = next_step(start, goal, walkable)
        detour_too_long = False
        if path is not None and self._creatures:
            short = next_step(start, goal, free)
            detour_too_long = short is not None and path[1] > short[1] + _DETOUR
        stuck = path is None or detour_too_long
        fight = self._state.monsters and not self.targeting_paused
        if stuck and fight and now < self._stuck_allowed_until:
            if not self.stuck_on_creatures:
                logger.info(
                    "cavebot: [stuck: creatures] creatures block the way, targeting takes over"
                )
            self.stuck_on_creatures = True
            self._target = None
            self.planned_path = []
            self.release_walk_key()
            self._stuck_allowed_until = now + _STUCK_WINDOW  # stay put while it fights
            through_creatures = next_step(start, goal, free)
            if through_creatures is not None:
                self._attack_creature_in_the_way(through_creatures[2], pos, now)
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
            step = self._best_unbumped_step(wp, pos, danger)
            self.planned_path = [(pos.x + step[0], pos.y + step[1])]
        else:
            step = path[0]
            self.planned_path = path[2]
        self._target = (pos.x + step[0], pos.y + step[1], pos.z)
        self._press_walk_key(step)

    def _creatures_moved(self, pos: Position, now: float) -> bool:
        """The creatures on screen moved since the last scan: the way is planned again.

        # doc: docs/prs/tunnel-fights.md#replan-on-creature-move
        """
        seen_before = self._creatures
        return self._scan(pos, now) and self._creatures != seen_before

    def _attack_creature_in_the_way(
        self, path: list[tuple[int, int]], pos: Position, now: float
    ) -> None:
        """Right-click the first creature on *path*: that one is attacked, not the top row."""
        if self._state.selected_target is not None or now < self.creature_in_the_way_clicked_until:
            return
        creature = next((tile for tile in path if tile in self._creatures), None)
        if creature is None:
            return  # the scan saw none on the way: the battle list picks
        logger.info(
            "cavebot: [stuck: creatures] attacking the creature in the way at %s,%s", *creature
        )
        self._clicker.request(self._tile_pixel(creature, pos), side=ClickSide.RIGHT)
        self.creature_in_the_way_clicked_until = now + _CREATURE_IN_THE_WAY_CLICK_WAIT

    def _tile_pixel(self, tile: tuple[int, int], pos: Position) -> tuple[int, int]:
        """Frame pixel at the middle of world *tile* on the game grid."""
        gx, gy, gw, gh = self._game_region
        tw, th = gw // GAME_GRID_COLS, gh // GAME_GRID_ROWS
        col = PLAYER_SLOT[0] + tile[0] - pos.x
        row = PLAYER_SLOT[1] + tile[1] - pos.y
        return gx + col * tw + tw // 2, gy + row * th + th // 2

    def _release_tiles_creatures_left(self) -> None:
        """A bumped tile is free again once the creature that stood on it walked off."""
        self._bumped_into_creature &= self._blocked.keys()
        for tile in list(self._blocked):
            if (tile[0], tile[1]) in self._creatures:
                self._bumped_into_creature.add(tile)
            elif tile in self._bumped_into_creature:
                del self._blocked[tile]
                self._bumped_into_creature.discard(tile)

    def _best_unbumped_step(
        self, wp: Waypoint, pos: Position, danger: set[tuple[int, int, int]]
    ) -> tuple[int, int]:
        """No path: the step nearest *wp*, safe unbumped tiles first (map data can be wrong)."""

        def rank(step: tuple[int, int]) -> tuple[bool, bool, int, int]:
            x, y = pos.x + step[0], pos.y + step[1]
            dx, dy = abs(wp.x - x), abs(wp.y - y)
            unsafe = (x, y, pos.z) in danger and (x, y) != (wp.x, wp.y)
            return unsafe, (x, y, pos.z) in self._blocked, max(dx, dy), dx + dy

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
        logger.info(
            "cavebot: [stuck: sidestep] blocked, sidestep %d to %+d,%+d", self._sidesteps, *side
        )
        mx, my, mw, mh = self._minimap_region
        self._clicker.cancel()
        self._clicker.request((mx + mw // 2 + side[0], my + mh // 2 + side[1]))

    def _tile_action(self, wp: Waypoint, pos: Position) -> None:
        tile = self._tile_pixel((wp.x, wp.y), pos)
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
