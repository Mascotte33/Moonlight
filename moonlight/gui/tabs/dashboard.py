"""Dashboard tab — live bot health at a glance (T39).

Shows bot tick count (from the loop's stats), creature/monster counts,
HP/MP, active modules, the live battle list, and a terminal-style event log
tail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui.tabs.base import Tab, section, stat_label

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState

__all__ = ["DashboardTab"]


class DashboardTab(Tab):
    """Live bot health: ticks, creatures, HP/MP, modules, battle list, log."""

    title = "Dashboard"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        self.lbl_tick = stat_label(parent, "Bot tick")
        self.lbl_tick.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 0))
        self.lbl_state = stat_label(parent, "Loop state")
        self.lbl_state.grid(row=0, column=1, sticky="w", padx=10)
        self.lbl_monsters = stat_label(parent, "Monsters")
        self.lbl_monsters.grid(row=0, column=2, sticky="w", padx=10)
        self.lbl_players = stat_label(parent, "Players")
        self.lbl_players.grid(row=0, column=3, sticky="w", padx=10)
        self.lbl_hp = stat_label(parent, "HP")
        self.lbl_hp.grid(row=0, column=4, sticky="w", padx=10)
        self.lbl_mp = stat_label(parent, "MP")
        self.lbl_mp.grid(row=0, column=5, sticky="w", padx=10)
        self.lbl_modules = stat_label(parent, "Modules")
        self.lbl_modules.grid(row=0, column=6, sticky="w", padx=10)

        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=3)
        body.grid_rowconfigure(2, weight=2)

        self.battle_box = section(body, "Battle list")
        self.battle_box.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        self.battle_list = ctk.CTkTextbox(self.battle_box, height=120, wrap="word")
        self.battle_list.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.battle_list.configure(state="disabled")

        self.log_box = section(body, "Event log")
        self.log_box.grid(row=2, column=0, sticky="nsew")
        self.log_view = ctk.CTkTextbox(self.log_box, height=100, wrap="none")
        self.log_view.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.log_view.configure(state="disabled")
        self._last_log: str = ""

    def _set_text(self, box: ctk.CTkTextbox, text: str) -> None:
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", text)
        box.configure(state="disabled")

    def _loop_stats(self) -> dict[str, object]:
        ctrl = self.ctx.controller
        if ctrl is None:
            return {}
        try:
            return ctrl.loop.get_stats()
        except Exception:  # noqa: BLE001 - display only
            return {}

    def on_poll(self, snap: WorldState) -> None:
        stats = self._loop_stats()
        self.lbl_tick.configure(text=f"Bot tick: {stats.get('tick_count', 0)}")
        self.lbl_state.configure(text=f"Loop state: {stats.get('state', 'idle')}")
        self.lbl_monsters.configure(text=f"Monsters: {len(snap.monsters)}")
        self.lbl_players.configure(text=f"Players: {snap.players_on_screen}")
        self.lbl_hp.configure(text=f"HP: {snap.hp_percent:.0f}%")
        self.lbl_mp.configure(text=f"MP: {snap.mp_percent:.0f}%")
        cfg = self.ctx.active_config()
        mods = [
            name
            for name, on in (
                ("healing", cfg.modules.healing),
                ("cavebot", cfg.modules.cavebot),
                ("loot", cfg.modules.loot),
                ("overlay", cfg.modules.overlay),
            )
            if on
        ]
        self.lbl_modules.configure(text=f"Modules: {', '.join(mods) if mods else 'none'}")
        if snap.monsters:
            monsters = snap.monsters[:20]
            lines = [
                f"{i + 1}. {m.name}  ({m.hp_percent:.0f}% HP)" for i, m in enumerate(monsters)
            ]
            if snap.selected_target is not None:
                lines.insert(0, f"> selected: {snap.selected_target.name}")
            self._set_text(self.battle_list, "\n".join(lines))
        elif self.battle_list.get("1.0", "end").strip() != "empty":
            self._set_text(self.battle_list, "empty")
        log_text = self.ctx.log.text(20)
        if log_text != self._last_log:
            self._set_text(self.log_view, log_text)
            self.log_view.see("end")
            self._last_log = log_text
