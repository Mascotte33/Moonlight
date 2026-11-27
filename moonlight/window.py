"""The game client's window: one interface, one adapter per OS.

Every module that finds, focuses or measures the client window goes through
:data:`WINDOWS`, chosen once at import: :class:`X11Windows` on Linux,
:class:`Win32Windows` on Windows, :class:`NoWindows` elsewhere. The OS quirks
live here (Cinnamon's focus timestamp is in ``x11.activate``; Windows restarts
the client between accounts, Linux logs in on the login screen).

Screen capture and keyboard/mouse input are not window things and stay in
``perception.capture`` and ``hardware``.
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from moonlight import x11

Rect = tuple[int, int, int, int]


class Windows(Protocol):
    """What the bot needs from the OS's windows."""

    #: Close and restart the client between accounts (else the next account
    #: logs in on the login screen the logout leaves).
    restarts_client: bool
    #: The official client install.
    client_exe: Path

    def find(self, title: str) -> int | None:
        """Id of the shown window titled *title* or starting with it."""

    def alive(self, wid: int) -> bool:
        """*wid* still names a window; False: find it again."""

    def focus(self, wid: int) -> bool:
        """Bring *wid* to the front (restored when minimized)."""

    def focused(self, title: str, handle: Callable[[], int | None]) -> bool:
        """The window titled *title* is in front; *handle* finds its id when needed."""

    def rect(self, wid: int) -> Rect | None:
        """Screen ``(left, top, width, height)`` of the game area (no frame)."""

    def title(self, wid: int) -> str | None:
        """*wid*'s title; None when it closed."""

    def titles(self) -> list[str]:
        """Titles of the shown windows, sorted."""

    def open(self, title: str) -> bool:
        """The client window shows (True when the OS cannot tell)."""

    def in_game(self, title: str) -> bool | None:
        """The client shows a character (its title says so); None: the title
        cannot tell, look at the screen."""


class X11Windows:
    """Linux (X11)."""

    restarts_client = False
    client_exe = Path.home() / "Tibia" / "Tibia"

    def find(self, title: str) -> int | None:
        return x11.find_window(title)

    def alive(self, wid: int) -> bool:  # noqa: ARG002 - find keeps its own cache
        return False

    def focus(self, wid: int) -> bool:
        return x11.activate(wid)

    def focused(self, title: str, handle: Callable[[], int | None]) -> bool:  # noqa: ARG002
        active = x11.active_title()
        return active is not None and (active == title or active.startswith(title))

    def rect(self, wid: int) -> Rect | None:
        return x11.window_rect(wid)

    def title(self, wid: int) -> str | None:
        return x11.title_of(wid)

    def titles(self) -> list[str]:
        return x11.window_titles()

    def open(self, title: str) -> bool:
        return self.find(title) is not None

    def in_game(self, title: str) -> bool | None:
        # doc: docs/prs/stamina-switch.md#login-on-start
        # The title, not the screen: a window over the client hides the game
        # but must never pull the client back to the front to "log in".
        wid = self.find(title)
        name = self.title(wid) if wid is not None else None
        return name is not None and name.startswith("Tibia - ")


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class Win32Windows:
    """Windows (user32)."""

    restarts_client = True
    client_exe = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Tibia" / "packages" / "Tibia" / "bin" / "client_launcher.exe"
    )  # fmt: skip

    @staticmethod
    def _user32() -> Any:
        return getattr(ctypes, "windll").user32  # noqa: B009 - Windows-only attribute

    def _text(self, wid: int) -> str:
        user32 = self._user32()
        length = user32.GetWindowTextLengthW(wid)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(wid, buf, length + 1)
        return buf.value

    def _each_shown(self, visit: Callable[[int], None]) -> None:
        user32 = self._user32()

        def cb(wid: int, _lparam: object) -> bool:
            if user32.IsWindowVisible(wid):
                visit(int(wid))
            return True

        # Keep a strong reference so the callback survives the EnumWindows call.
        winfunctype = getattr(ctypes, "WINFUNCTYPE")  # noqa: B009 - Windows-only attribute
        proc = winfunctype(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(cb)
        user32.EnumWindows(proc, None)

    def find(self, title: str) -> int | None:
        """An exact match (``FindWindowW``) first, else the first shown window
        whose title starts with *title* ("Tibia - <character>")."""
        user32 = self._user32()
        user32.FindWindowW.restype = ctypes.c_void_p
        wid = user32.FindWindowW(None, title)
        if wid:
            return int(wid)
        found: list[int] = []

        def visit(w: int) -> None:
            name = self._text(w)
            if name and (name == title or name.startswith(title)):
                found.append(w)

        self._each_shown(visit)
        return found[0] if found else None

    def alive(self, wid: int) -> bool:
        return bool(self._user32().IsWindow(ctypes.c_void_p(wid)))

    def focus(self, wid: int) -> bool:
        user32 = self._user32()
        if user32.IsIconic(wid):
            user32.ShowWindow(wid, 9)  # SW_RESTORE
        return bool(user32.SetForegroundWindow(wid))

    def focused(self, title: str, handle: Callable[[], int | None]) -> bool:  # noqa: ARG002
        wid = handle()
        if wid is None:
            return False
        user32 = self._user32()
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        if not user32.IsWindowVisible(wid):
            return False
        return int(wid) == int(user32.GetForegroundWindow() or 0)

    # doc: docs/prs/gui-calibration.md#game-area-coordinates
    def rect(self, wid: int) -> Rect | None:
        user32 = self._user32()
        rect = _RECT()
        if not user32.GetClientRect(ctypes.c_void_p(wid), ctypes.byref(rect)):
            return None
        origin = _POINT(0, 0)
        if not user32.ClientToScreen(ctypes.c_void_p(wid), ctypes.byref(origin)):
            return None
        if rect.right <= 0 or rect.bottom <= 0:
            return None
        return (origin.x, origin.y, rect.right, rect.bottom)

    def title(self, wid: int) -> str | None:
        return self._text(wid) if self.alive(wid) else None

    def titles(self) -> list[str]:
        names: set[str] = set()
        self._each_shown(lambda w: names.add(self._text(w)))
        return sorted(n for n in names if n)

    def open(self, title: str) -> bool:  # noqa: ARG002 - the launcher is titled "Tibia" too
        return True

    def in_game(self, title: str) -> bool | None:  # noqa: ARG002
        return None


class NoWindows:
    """Any other OS: no window is ever found."""

    restarts_client = False
    client_exe = Path("Tibia")

    def find(self, title: str) -> int | None:  # noqa: ARG002
        return None

    def alive(self, wid: int) -> bool:  # noqa: ARG002
        return False

    def focus(self, wid: int) -> bool:  # noqa: ARG002
        return False

    def focused(self, title: str, handle: Callable[[], int | None]) -> bool:  # noqa: ARG002
        return False

    def rect(self, wid: int) -> Rect | None:  # noqa: ARG002
        return None

    def title(self, wid: int) -> str | None:  # noqa: ARG002
        return None

    def titles(self) -> list[str]:
        return []

    def open(self, title: str) -> bool:  # noqa: ARG002
        return True

    def in_game(self, title: str) -> bool | None:  # noqa: ARG002
        return None


def for_platform(platform: str = sys.platform) -> Windows:
    """The adapter for *platform*."""
    if platform == "linux":
        return X11Windows()
    if platform == "win32":
        return Win32Windows()
    return NoWindows()


#: This OS's windows.
WINDOWS: Windows = for_platform()


def focus_window(title: str) -> bool:
    """Bring the window *title* to the front; False when it is not found."""
    wid = WINDOWS.find(title)
    return wid is not None and WINDOWS.focus(wid)
