"""Route recording helpers for the Cavebot route editor (T8.F1.K1-T8.F4.K1, T3.F9.K1).

Routes are the bot's own files, ``<routes_dir>/<name>.json``, in the format
:class:`RouteFileLoader` reads, so the Cavebot tab can pick and run them.
"""

from __future__ import annotations

import ctypes
import json
import queue
import re
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

from moonlight import x11
from moonlight.cavebot.cavebot import RouteError, RouteFileLoader, Waypoint
from moonlight.core.state import Position

__all__ = [
    "HOTKEYS",
    "Hotkeys",
    "make_waypoint",
    "route_path",
    "StandTracker",
    "save_route",
    "should_add",
    "should_stop_recording",
    "position_line",
    "vk_code",
]

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")
#: Recording hotkeys: function key -> waypoint action (or "undo").
HOTKEYS = {"F4": "ladder", "F5": "undo", "F6": "walk", "F7": "rope", "F8": "shovel", "F9": "label"}
_WM_QUIT = 0x0012
_WM_HOTKEY = 0x0312
_MOD_NOREPEAT = 0x4000


def vk_code(key: str) -> int:
    """Windows virtual-key code of function key *key* ("F1" is 0x70)."""
    return 0x6F + int(key[1:])


class Hotkeys:
    """:data:`HOTKEYS` as global hotkeys, pressed actions put on *out*.

    ``RegisterHotKey`` ties keys to the thread that registers them, so they
    live on their own thread with a message loop. Linux has no such call, so
    a thread polls the X11 key map instead. There the key also reaches the
    game, so keep F4 to F9 free of client hotkeys while recording.
    """

    def __init__(self, out: queue.Queue[str]) -> None:
        self._out = out
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._stop = threading.Event()
        self.failed: list[str] = []

    def start(self) -> None:
        if self._thread is not None:
            return
        if sys.platform == "linux":
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._poll, daemon=True, name="recorder-keys")
            self._thread.start()
            return
        if sys.platform != "win32":
            return
        ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(ready,), daemon=True, name="recorder-hotkeys"
        )
        self._thread.start()
        ready.wait(1.0)

    def stop(self) -> None:
        if self._thread is None:
            return
        if sys.platform == "linux":
            self._stop.set()
            self._thread.join(1.0)
            self._thread = None
            return
        if sys.platform != "win32":
            return
        ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
        self._thread.join(1.0)
        self._thread = None

    def _poll(self) -> None:
        held = {k: False for k in HOTKEYS}
        while not self._stop.wait(0.03):
            for k, was in held.items():
                down = x11.vk_down(vk_code(k))
                if down and not was:
                    self._out.put(HOTKEYS[k])
                held[k] = down

    def _run(self, ready: threading.Event) -> None:
        if sys.platform != "win32":
            return
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # makes the message queue
        keys = list(HOTKEYS)
        self.failed = [
            k
            for i, k in enumerate(keys)
            if not user32.RegisterHotKey(None, i + 1, _MOD_NOREPEAT, vk_code(k))
        ]
        ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == _WM_HOTKEY and 1 <= msg.wParam <= len(keys):
                self._out.put(HOTKEYS[keys[msg.wParam - 1]])
        for i in range(len(keys)):
            user32.UnregisterHotKey(None, i + 1)


class StandTracker:
    """The tile the character stood on, not the one it is walking to.

    The minimap re-centers on the destination as soon as a step starts, while
    the character is still on the tile it left. So a position read is only
    trusted once it held for *settle* seconds; until then the tile the
    character left is the confirmed one.
    """

    def __init__(self, settle: float = 0.6, clock: Callable[[], float] = time.monotonic) -> None:
        self._settle = settle
        self._clock = clock
        self._current: Position | None = None
        self._previous: Position | None = None
        self._since = 0.0

    def update(self, pos: Position) -> None:
        """Feed the position read this tick; (0, 0, 0) is no read."""
        if (pos.x, pos.y, pos.z) == (0, 0, 0) or pos == self._current:
            return
        self._previous, self._current = self._current, pos
        self._since = self._clock()

    @property
    def stood(self) -> Position | None:
        """The current tile once it held for *settle* seconds, else the tile left (or None)."""
        if self._current is not None and self._clock() - self._since >= self._settle:
            return self._current
        return self._previous


def make_waypoint(
    action: str, label: str, manual: tuple[str, str, str], live: Position
) -> Waypoint:
    """A waypoint for *action*: at the *manual* x, y, z when all are given, else at *live*.

    Raises:
        RouteError: For a missing label, bad numbers or an unknown position.
    """
    if action in ("label", "goto"):
        if not label.strip():
            raise RouteError(f"{action} needs a label")
        return Waypoint(0, 0, 0, action, label.strip())
    if all(v.strip() for v in manual):
        try:
            x, y, z = (int(v) for v in manual)
        except ValueError:
            raise RouteError("X, Y and Z must be whole numbers") from None
        return Waypoint(x, y, z, action)
    if (live.x, live.y, live.z) == (0, 0, 0):
        raise RouteError("Position unknown. Type X, Y and Z, or start the bot.")
    return Waypoint(live.x, live.y, live.z, action)


def should_add(waypoints: list[Waypoint], pos: Position, min_tiles: int) -> bool:
    """True when *pos* is *min_tiles* from the last positioned waypoint or on a new floor."""
    if (pos.x, pos.y, pos.z) == (0, 0, 0):
        return False
    last = next((wp for wp in reversed(waypoints) if wp.action not in ("label", "goto")), None)
    if last is None or last.z != pos.z:
        return True
    return max(abs(pos.x - last.x), abs(pos.y - last.y)) >= min_tiles


def should_stop_recording(recording: bool, cavebot_on: bool) -> bool:
    """True when a recording is running and the cavebot has started walking a route."""
    return recording and cavebot_on


def position_line(pos: Position, status: str = "") -> str:
    """The character's coordinates and *status*, or "unknown" before the bot reads them."""
    if (pos.x, pos.y, pos.z) == (0, 0, 0):
        return "Position unknown"
    tail = f" ({status})" if status else ""
    return f"Position {pos.x}, {pos.y}, {pos.z}{tail}"


def route_path(routes_dir: Path, name: str) -> Path:
    """The file of route *name* in *routes_dir*.

    Raises:
        RouteError: For a name that is not a plain file name.
    """
    if not _NAME_RE.match(name):
        raise RouteError("route name: letters, digits, space, '.', '_' or '-'")
    return Path(routes_dir) / f"{name}.json"


def save_route(routes_dir: Path, name: str, waypoints: list[Waypoint]) -> Path:
    """Write *waypoints* as route *name* in *routes_dir*; the file path.

    Raises:
        RouteError: For a bad name or a route the bot would refuse.
    """
    path = route_path(routes_dir, name)
    raw = {
        "name": name,
        "waypoints": [
            {"action": wp.action, "label": wp.label}
            if wp.action in ("label", "goto")
            else {"x": wp.x, "y": wp.y, "z": wp.z, "action": wp.action}
            for wp in waypoints
        ],
    }
    RouteFileLoader.parse(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return path
