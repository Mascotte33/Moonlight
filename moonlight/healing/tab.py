"""Healing tab: the spell and potion cooldowns and the live HP/MP label.

# doc: docs/prs/healing.md#healing-rules

The rules and cures editor comes with H1.F3.K1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui import theme
from moonlight.gui.tab_base import Option, Tab, section

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.core.state import WorldState

__all__ = ["OPTIONS", "HealingTab"]

OPTIONS = [
    Option("Spell cooldown (ms)", "healing.spell_cooldown_ms", "slider", min=100, max=2000),
    Option("Potion cooldown (ms)", "healing.potion_cooldown_ms", "slider", min=100, max=2000),
]


class HealingTab(Tab):
    """Healing cooldowns, saved into the picked config."""

    title = "Healing"
    options = OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        box = section(parent, "Cooldowns")
        box.pack(fill="x", padx=10, pady=(10, 0))
        self.build_options(box, OPTIONS)
        self.lbl_live = ctk.CTkLabel(
            parent, text="", anchor="w", font=ctk.CTkFont(family=theme.MONO)
        )
        self.lbl_live.pack(fill="x", padx=10)

    def on_poll(self, snap: WorldState) -> None:
        text = f"live HP {snap.hp_percent:.0f}%  MP {snap.mp_percent:.0f}%"
        if self.lbl_live.cget("text") != text:
            self.lbl_live.configure(text=text)
