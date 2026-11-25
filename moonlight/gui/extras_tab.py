"""Extras and Alarms tabs: one hunt's extras and alarms, saved in the picked profile.

They used to be boxes on the Settings tab, which now keeps only this PC's
settings (docs/prs/profiles.md#layout).
"""

from __future__ import annotations

import customtkinter as ctk

from moonlight.core.state import WorldState
from moonlight.gui.settings_tab import ALARMS_OPTIONS, EXTRAS_OPTIONS
from moonlight.gui.tab_base import Tab, section

__all__ = ["AlarmsTab", "ExtrasTab"]


class ExtrasTab(Tab):
    """Food, haste, buffs, ring and amulet."""

    title = "Extras"
    options = EXTRAS_OPTIONS
    #: The food watch below runs while the tab is hidden too.
    polls_hidden = True

    def build(self, parent: ctk.CTkFrame) -> None:
        box = section(parent, "Extras")
        box.pack(fill="x", padx=10, pady=(10, 4))
        self.build_options(box)

    def on_poll(self, snap: WorldState) -> None:
        # doc: docs/prs/near-targeting.md#food-pause
        extras = self.ctx.live_module("extras")
        if extras is not None and not extras.config.food and self._readers["extras.food"]():
            self._writers["extras.food"](False)
            self.submit()


class AlarmsTab(Tab):
    """Low HP, fast HP drop, players and private messages."""

    title = "Alarms"
    options = ALARMS_OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        box = section(parent, "Alarms")
        box.pack(fill="x", padx=10, pady=(10, 4))
        self.build_options(box)
