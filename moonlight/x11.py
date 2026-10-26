"""The Linux (X11) side of the desktop calls the bot makes on Windows through ``user32``.

Windows, cursor, focus and key state through ``python-xlib`` on one shared
connection. Wayland is not supported: it hides other windows and the cursor.
Every call returns ``None`` / ``False`` / ``[]`` when X is missing or a
window closed mid-call, like the Windows code does off Windows.
"""

# doc: docs/prs/linux.md#x11-desktop

from __future__ import annotations

import threading
from typing import Any

__all__ = ["active_title", "cursor", "find_window", "vk_down", "window_rect", "window_titles"]

_lock = threading.Lock()  # one Display, used from the loop, F12 and GUI threads
_display: Any = None
_cached: dict[str, int] = {}  # title -> window id, checked before a full scan


def _d() -> Any:
    """The shared Display, opened on first use; None without X."""
    global _display
    if _display is None:
        try:
            from Xlib import display

            _display = display.Display()
        except Exception:  # noqa: BLE001 - no X server, no python-xlib
            return None
    return _display


def _title(d: Any, wid: int) -> str:
    win = d.create_resource_object("window", wid)
    prop = win.get_full_property(d.intern_atom("_NET_WM_NAME"), d.intern_atom("UTF8_STRING"))
    if prop is not None and prop.value:
        value = prop.value
        return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    name = win.get_wm_name()
    return name if isinstance(name, str) else ""


def _clients(d: Any) -> list[int]:
    from Xlib import X

    prop = d.screen().root.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
    return list(prop.value) if prop is not None else []


def _viewable(d: Any, wid: int) -> bool:
    from Xlib import X

    return bool(d.create_resource_object("window", wid).get_attributes().map_state == X.IsViewable)


def _matches(title: str, wanted: str) -> bool:
    return title == wanted or title.startswith(wanted)


def find_window(title: str) -> int | None:
    """Id of the first shown window whose title is or starts with *title*."""
    with _lock:
        d = _d()
        if d is None:
            return None
        try:
            wid = _cached.get(title)
            if wid is not None and _viewable(d, wid) and _matches(_title(d, wid), title):
                return wid
            for wid in _clients(d):
                if _viewable(d, wid) and _matches(_title(d, wid), title):
                    _cached[title] = wid
                    return wid
        except Exception:  # noqa: BLE001 - BadWindow when a window closes mid-scan
            _cached.pop(title, None)
        return None


def window_rect(wid: int) -> tuple[int, int, int, int] | None:
    """Screen ``(left, top, width, height)`` of the window, frame excluded."""
    with _lock:
        d = _d()
        if d is None:
            return None
        try:
            win = d.create_resource_object("window", wid)
            geom = win.get_geometry()
            origin = d.screen().root.translate_coords(win, 0, 0)
        except Exception:  # noqa: BLE001
            return None
        if geom.width <= 0 or geom.height <= 0:
            return None
        return (int(origin.x), int(origin.y), int(geom.width), int(geom.height))


def window_titles() -> list[str]:
    """Titles of the shown windows, sorted."""
    with _lock:
        d = _d()
        if d is None:
            return []
        try:
            titles = {_title(d, w) for w in _clients(d) if _viewable(d, w)}
        except Exception:  # noqa: BLE001
            return []
        return sorted(t for t in titles if t)


def active_title() -> str | None:
    """Title of the focused window."""
    from Xlib import X

    with _lock:
        d = _d()
        if d is None:
            return None
        try:
            prop = d.screen().root.get_full_property(
                d.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType
            )
            if prop is None or not prop.value or not prop.value[0]:
                return None
            return _title(d, int(prop.value[0]))
        except Exception:  # noqa: BLE001
            return None


def cursor() -> tuple[int, int] | None:
    """Mouse position on the screen."""
    with _lock:
        d = _d()
        if d is None:
            return None
        try:
            p = d.screen().root.query_pointer()
        except Exception:  # noqa: BLE001
            return None
        return (int(p.root_x), int(p.root_y))


def vk_down(vk: int) -> bool:
    """True while the function key with Windows code *vk* (F1 0x70 .. F24 0x87) is held."""
    if not 0x70 <= vk <= 0x87:
        return False
    with _lock:
        d = _d()
        if d is None:
            return False
        try:
            from Xlib import XK

            code = d.keysym_to_keycode(XK.string_to_keysym(f"F{vk - 0x6F}"))
            keys = d.query_keymap()
        except Exception:  # noqa: BLE001
            return False
        return bool(code) and bool(keys[code // 8] >> (code % 8) & 1)
