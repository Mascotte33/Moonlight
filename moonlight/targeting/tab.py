"""Targeting tab: the attack hotkey and the shared attack cooldown.

Monster rows and area rules are edited in the config file until the rows
table lands (T1.F9.K1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui import theme
from moonlight.gui.tab_base import Option, Tab, section

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.core.state import WorldState

__all__ = ["OPTIONS", "TargetingTab"]

OPTIONS = [
    Option("Attack hotkey", "loop.attack_combo", "hotkey"),
    Option("Attack cooldown (ms)", "targeting.attack_cooldown_ms", "slider", min=0, max=5000),
]


class TargetingTab(Tab):
    """Targeting options, saved into the picked config."""

    title = "Targeting"
    options = OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        box = section(parent, "Targeting")
        box.pack(fill="x", padx=10, pady=10)
        self.build_options(box)
        self.lbl_live = ctk.CTkLabel(parent, text="", anchor="w", text_color=theme.TEXT_DIM)
        self.lbl_live.pack(fill="x", padx=10)

    def on_poll(self, snap: WorldState) -> None:
        names = ", ".join(m.name for m in snap.monsters) or "none"
        text = f"creatures on screen: {names}"
        if self.lbl_live.cget("text") != text:
            self.lbl_live.configure(text=text)
