"""Status tab (T39) — live WorldState and loop statistics.

Shows HP/MP bars, monster list, selected target, minimap position,
inventory capacity, active buffs, and the bot loop's own stats
(tick count, uptime, actions sent/dropped).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui.tabs.base import Tab, section, stat_label, stat_num

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.state import WorldState

__all__ = ["StatusTab"]


class StatusTab(Tab):
    """Live WorldState view: vitals, battle, position, capacity, buffs, loop."""

    title = "Status"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=2)
        parent.grid_rowconfigure(1, weight=2)
        parent.grid_rowconfigure(2, weight=3)

        vit = section(parent, "Vitals")
        vit.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 6))
        vit.grid_columnconfigure(0, weight=1)
        self.hp_bar = ctk.CTkProgressBar(vit, progress_color="#2ecc71")
        self.hp_bar.set(0)
        self.hp_bar.pack(fill="x", padx=10, pady=(6, 0))
        self.lbl_hp = ctk.CTkLabel(vit, text="HP: --")
        self.lbl_hp.pack(anchor="w", padx=10, pady=(0, 6))
        self.mp_bar = ctk.CTkProgressBar(vit, progress_color="#3498db")
        self.mp_bar.set(0)
        self.mp_bar.pack(fill="x", padx=10, pady=(0, 0))
        self.lbl_mp = ctk.CTkLabel(vit, text="MP: --")
        self.lbl_mp.pack(anchor="w", padx=10, pady=(0, 6))
        self.lbl_screen = stat_label(vit, "Screen / focus")
        self.lbl_screen.pack(anchor="w", padx=10, pady=(0, 6))

        battle = section(parent, "Battle")
        battle.grid(row=0, column=1, sticky="nsew", padx=(0, 10), pady=(10, 6))
        battle.grid_columnconfigure(0, weight=1)
        self.lbl_target = stat_label(battle, "Selected target")
        self.lbl_target.pack(anchor="w", padx=10, pady=(6, 0))
        self.mon_box = ctk.CTkTextbox(battle, height=120, wrap="word")
        self.mon_box.pack(fill="both", expand=True, padx=10, pady=(4, 8))
        self.mon_box.configure(state="disabled")
        self._mon_text: str = ""

        pos = section(parent, "Position & inventory")
        pos.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=10, pady=6)
        pos_grid = ctk.CTkFrame(pos, fg_color="transparent")
        pos_grid.pack(fill="both", expand=True)
        pos_grid.grid_columnconfigure((0, 1), weight=1)
        self.lbl_position = stat_label(pos_grid, "Minimap position")
        self.lbl_position.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        self.lbl_capacity = stat_label(pos_grid, "Capacity")
        self.lbl_capacity.grid(row=0, column=1, sticky="ew", padx=10, pady=(8, 2))
        self.lbl_buffs = stat_label(pos_grid, "Buffs")
        self.lbl_buffs.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))

        loop = section(parent, "Loop stats")
        loop.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=10, pady=(0, 10))
        loop_grid = ctk.CTkFrame(loop, fg_color="transparent")
        loop_grid.pack(fill="both", expand=True)
        loop_grid.grid_columnconfigure((0, 1), weight=1)
        self.lbl_state = stat_label(loop_grid, "Loop state")
        self.lbl_state.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        self.lbl_ticks = stat_label(loop_grid, "Ticks")
        self.lbl_ticks.grid(row=0, column=1, sticky="ew", padx=10, pady=(8, 2))
        self.lbl_uptime = stat_label(loop_grid, "Uptime")
        self.lbl_uptime.grid(row=1, column=0, sticky="ew", padx=10)
        self.lbl_actions = stat_label(loop_grid, "Actions sent")
        self.lbl_actions.grid(row=1, column=1, sticky="ew", padx=10)
        self.lbl_dropped = stat_label(loop_grid, "Dropped")
        self.lbl_dropped.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))

    def _set_text(self, box: ctk.CTkTextbox, text: str) -> None:
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", text)
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
        hp = snap.hp_percent
        mp = snap.mp_percent
        self.hp_bar.set(max(0.0, min(1.0, hp / 100.0)))
        self.mp_bar.set(max(0.0, min(1.0, mp / 100.0)))
        self.lbl_hp.configure(text=f"HP: {hp:.0f}%")
        self.lbl_mp.configure(text=f"MP: {mp:.0f}%")
        screen = "ok" if snap.screen_ok else "missing"
        focus = "focused" if snap.client_focused else "unfocused"
        self.lbl_screen.configure(text=f"Screen / focus: {screen}, {focus}")

        target = snap.selected_target
        target_text = (
            f"{target.name} ({target.hp_percent:.0f}%)" if target else "--"
        )
        if self.lbl_target.cget("text") != f"Selected target: {target_text}":
            self.lbl_target.configure(text=f"Selected target: {target_text}")

        mon = "\n".join(
            f"{m.name}  {m.hp_percent:.0f}%" + ("  [player]" if m.is_player else "")
            for m in snap.monsters
        ) or "none on screen"
        if mon != self._mon_text:
            self._mon_text = mon
            self._set_text(self.mon_box, mon)

        pos = snap.position
        self.lbl_position.configure(
            text=f"Minimap position: {pos.x}, {pos.y} (z {pos.z})"
        )
        cap = snap.capacity
        self.lbl_capacity.configure(
            text=f"Capacity: {cap.weight:.1f} / {cap.max_weight:.1f}"
        )
        buffs = ", ".join(
            f"{name} {secs:.0f}s" for name, secs in snap.buffs.items()
        ) or "none"
        if self.lbl_buffs.cget("text") != f"Buffs: {buffs}":
            self.lbl_buffs.configure(text=f"Buffs: {buffs}")

        stats = self._loop_stats()
        if stats:
            ticks = int(stat_num(stats, "tick_count"))
            uptime = stat_num(stats, "uptime_seconds")
            sent = int(stat_num(stats, "actions_sent"))
            dropped = (
                stat_num(stats, "actions_dropped_rate_limit")
                + stat_num(stats, "actions_dropped_unfocused")
            )
            mm, ss = divmod(int(uptime), 60)
            self.lbl_state.configure(text=f"Loop state: {stats.get('state', 'idle')}")
            self.lbl_ticks.configure(text=f"Ticks: {ticks}")
            self.lbl_uptime.configure(text=f"Uptime: {mm:02d}:{ss:02d}")
            self.lbl_actions.configure(text=f"Actions sent: {sent}")
            self.lbl_dropped.configure(text=f"Dropped: {dropped}")
