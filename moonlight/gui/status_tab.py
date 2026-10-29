"""Status tab (T39) — live WorldState and loop statistics.

Shows HP/MP bars, monster list, selected target, minimap position,
inventory capacity, the bot loop's own stats
(tick count, uptime, actions sent/dropped) and a System card (T9.F1.K1).
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui import sysinfo, theme
from moonlight.gui.tab_base import Tab, section, stat_label, stat_num

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.core.state import WorldState

__all__ = ["StatusTab", "age_text"]


def age_text(last_update: float, now: float) -> str:
    """How long ago ``last_update`` was, e.g. "updated 3 s ago"; "never" at 0."""
    if last_update <= 0:
        return "never"
    return f"updated {max(0, int(now - last_update))} s ago"


class StatusTab(Tab):
    """Live WorldState view: vitals, battle, position, capacity, loop."""

    title = "Status"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=2)
        parent.grid_rowconfigure(1, weight=2)
        parent.grid_rowconfigure(2, weight=3)
        parent.grid_rowconfigure(3, weight=1)

        vit = section(parent, "Vitals")
        vit.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 6))
        vit.grid_columnconfigure(0, weight=1)
        self.hp_bar = ctk.CTkProgressBar(vit, progress_color=theme.LIFE)
        self.hp_bar.set(0)
        self.hp_bar.pack(fill="x", padx=10, pady=(6, 0))
        self.lbl_hp = ctk.CTkLabel(vit, text="HP: --")
        self.lbl_hp.pack(anchor="w", padx=10, pady=(0, 6))
        self.mp_bar = ctk.CTkProgressBar(vit, progress_color=theme.MANA)
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
        row = ctk.CTkFrame(pos_grid, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        self.lbl_position = ctk.CTkLabel(
            row, text="--", anchor="w", font=ctk.CTkFont(family=theme.MONO, size=theme.SIZE_HEADING)
        )
        self.lbl_position.pack(side="left")
        ctk.CTkButton(
            row,
            text="Copy",
            width=60,
            command=self._copy_position,
            fg_color=theme.RAISED,
            hover_color=theme.HOVER,
        ).pack(side="left", padx=theme.UNIT)
        self.lbl_age = ctk.CTkLabel(row, text="never", anchor="w", text_color=theme.TEXT_MUTED)
        self.lbl_age.pack(side="left", padx=theme.UNIT)
        self._parent = parent
        self.lbl_capacity = stat_label(pos_grid, "Capacity")
        self.lbl_capacity.grid(row=0, column=1, sticky="ew", padx=10, pady=(8, 2))

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

        system = section(parent, "System")
        system.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=10, pady=(0, 10))
        self.cpu_bar = ctk.CTkProgressBar(system, progress_color=theme.AMBER)
        self.cpu_bar.set(0)
        self.cpu_bar.pack(fill="x", padx=10, pady=(6, 0))
        self.lbl_cpu = stat_label(system, "CPU")
        self.lbl_cpu.pack(anchor="w", padx=10)
        self.ram_bar = ctk.CTkProgressBar(system, progress_color=theme.MANA)
        self.ram_bar.set(0)
        self.ram_bar.pack(fill="x", padx=10)
        self.lbl_ram = stat_label(system, "RAM")
        self.lbl_ram.pack(anchor="w", padx=10)
        self.lbl_proc = stat_label(system, "Bot")
        self.lbl_proc.pack(anchor="w", padx=10, pady=(0, 8))
        self._sys_t = 0.0
        self._cpu = sysinfo.cpu_times()

    def _copy_position(self) -> None:
        self._parent.clipboard_clear()
        self._parent.clipboard_append(self.lbl_position.cget("text"))

    def _update_system(self) -> None:
        now = time.monotonic()
        if now - self._sys_t < 1.0:  # same pace as the bottom bar
            return
        cpu = sysinfo.cpu_times()
        if cpu is None or self._cpu is None:
            self.lbl_cpu.configure(text="CPU: —")
        else:
            busy = sysinfo.cpu_percent(self._cpu, cpu)
            self.cpu_bar.set(busy / 100.0)
            self.lbl_cpu.configure(text=f"CPU: {busy:.0f}%")
        ram = sysinfo.ram_percent()
        self.ram_bar.set(0 if ram is None else ram / 100.0)
        self.lbl_ram.configure(text="RAM: —" if ram is None else f"RAM: {ram:.0f}%")
        self.lbl_proc.configure(
            text=f"Bot: {sysinfo.memory_text()}, {threading.active_count()} threads"
        )
        self._sys_t, self._cpu = now, cpu

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
        self._update_system()
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
        text = f"{pos.x}, {pos.y}, {pos.z}"
        if self.lbl_position.cget("text") != text:
            self.lbl_position.configure(text=text)
        age = age_text(snap.last_update, time.monotonic())
        if self.lbl_age.cget("text") != age:
            self.lbl_age.configure(text=age)
        cap = snap.capacity
        self.lbl_capacity.configure(
            text=f"Capacity: {cap.weight:.1f} / {cap.max_weight:.1f}"
        )

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
