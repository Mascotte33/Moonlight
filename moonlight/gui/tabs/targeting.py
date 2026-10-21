"""Targeting tab: engagement rules and the loop's attack hotkey (T5.F1.K1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui import theme
from moonlight.gui.tabs.base import Option, Tab, section

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState

__all__ = ["OPTIONS", "TargetingTab"]

OPTIONS = [
    Option("Targeting", "targeting.enabled", "switch"),
    Option("Max monsters to engage", "targeting.max_monsters_to_engage", "slider", min=1, max=10),
    Option("Prefer lowest-HP monster", "targeting.prefer_low_hp", "switch"),
    Option("Flee when a player is on screen", "targeting.player_protection", "switch"),
    Option("Attack hotkey", "loop.attack_combo", "entry"),
]


class TargetingTab(Tab):
    """Targeting rules, saved into the picked config."""

    title = "Targeting"
    options = OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        box = section(parent, "Targeting")
        box.pack(fill="x", padx=10, pady=10)
        self.build_options(box)
        self.lbl_live = ctk.CTkLabel(
            parent, text="", anchor="w", text_color=theme.TEXT_DIM
        )
        self.lbl_live.pack(fill="x", padx=10)

    def on_poll(self, snap: WorldState) -> None:
        names = ", ".join(m.name for m in snap.monsters) or "none"
        text = f"creatures on screen: {names}"
        if self.lbl_live.cget("text") != text:
            self.lbl_live.configure(text=text)
