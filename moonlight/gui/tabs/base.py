"""Tab base class and small shared widget helpers (T39).

Every tab:

* receives a :class:`moonlight.gui.state_bridge.GuiContext` in its
  constructor (dependency injection — no globals, no display assumptions),
* builds its widgets in :meth:`build` (called once by the app shell),
* updates them in :meth:`on_poll` (called on every state poll, ~10-20 Hz).

``on_poll`` must stay fast (label/progress-bar updates only) — it runs on
the Tk thread while the bot loop thread keeps ticking in the background.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui import theme

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState
    from moonlight.gui.state_bridge import GuiContext

__all__ = ["Tab", "section", "stat_label", "stat_num"]


def section(parent: ctk.CTkFrame, title: str) -> ctk.CTkFrame:
    """A titled, inset frame (consistent look across tabs)."""
    box = ctk.CTkFrame(
        parent,
        fg_color=theme.PANEL,
        border_width=1,
        border_color=theme.RAISED,
        corner_radius=theme.RADIUS_CARD,
    )
    ctk.CTkLabel(
        box,
        text=title.upper(),
        anchor="w",
        font=ctk.CTkFont(size=theme.SIZE_TITLE, weight="bold"),
        text_color=theme.TEXT_MUTED,
    ).pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 0))
    return box


def stat_label(parent: ctk.CTkFrame, title: str) -> ctk.CTkLabel:
    """A right-aligned stat label (value updated in on_poll)."""
    return ctk.CTkLabel(
        parent,
        text=f"{title}: --",
        anchor="w",
        font=ctk.CTkFont(family=theme.MONO, size=theme.SIZE_BODY),
    )


def stat_num(stats: dict[str, object], key: str, default: float = 0.0) -> float:
    """Read a numeric field from a loop-stats dict (values are typed object)."""
    value = stats.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


class Tab:
    """Base class for all 10 GUI tabs.

    Subclasses set :attr:`title` and implement :meth:`build`;
    :meth:`on_poll` is a no-op by default.
    """

    title: str = "Tab"

    def __init__(self, ctx: GuiContext) -> None:
        self.ctx = ctx

    def build(self, parent: ctk.CTkFrame) -> None:
        """Create this tab's widgets inside *parent* (called once)."""
        raise NotImplementedError

    def on_poll(self, snap: WorldState) -> None:
        """Update widgets from a state snapshot (default: no-op)."""
        return None
