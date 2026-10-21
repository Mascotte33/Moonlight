"""Dashboard tab: stat cards, bot health, battle list and log tail (T1).

Four stat cards (Uptime, Ticks, Creatures, HP) show hero-size values from
the loop's stats and the live :class:`WorldState`. The module switches live
in Settings.
"""

from __future__ import annotations

import customtkinter as ctk

from moonlight.bot.state import WorldState
from moonlight.gui import theme
from moonlight.gui.tabs.base import Tab, dot_color, health, section, stat_num

__all__ = ["CARDS", "DashboardTab", "card_values", "uptime_text"]

CARDS = ("Uptime", "Ticks", "Creatures", "HP")


def uptime_text(stats: dict[str, object]) -> str:
    """``h:mm:ss`` from the loop's ``uptime_seconds``; ``—`` when stopped."""
    if "uptime_seconds" not in stats:
        return "—"
    secs = int(stat_num(stats, "uptime_seconds"))
    return f"{secs // 3600}:{secs // 60 % 60:02d}:{secs % 60:02d}"


def card_values(stats: dict[str, object], snap: WorldState) -> dict[str, str]:
    """The text of each stat card."""
    return {
        "Uptime": uptime_text(stats),
        "Ticks": str(int(stat_num(stats, "tick_count"))) if stats else "—",
        "Creatures": str(len(snap.monsters)),
        "HP": f"{snap.hp_percent:.0f}%",
    }


class DashboardTab(Tab):
    """Stat cards, battle list and event log."""

    title = "Dashboard"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        cards = ctk.CTkFrame(parent, fg_color="transparent")
        cards.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        self._cards: dict[str, ctk.CTkLabel] = {}
        for col, name in enumerate(CARDS):
            cards.grid_columnconfigure(col, weight=1, uniform="card")
            box = section(cards, name)
            box.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else theme.UNIT, 0))
            value = ctk.CTkLabel(
                box,
                text="—",
                anchor="w",
                font=ctk.CTkFont(family=theme.MONO, size=theme.SIZE_HERO, weight="bold"),
                text_color=theme.TEXT,
            )
            value.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))
            self._cards[name] = value

        self.lbl_task = ctk.CTkLabel(
            cards, text="Current task: Idle", anchor="w", text_color=theme.TEXT_DIM
        )
        self.lbl_task.grid(row=1, column=0, columnspan=len(CARDS), sticky="ew", pady=theme.UNIT)
        box = section(cards, "Bot health")
        box.grid(row=2, column=0, columnspan=len(CARDS), sticky="ew")
        dots = ctk.CTkFrame(box, fg_color="transparent")
        dots.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))
        self._dots: dict[str, ctk.CTkLabel] = {}
        for name, _ in health(WorldState(), False, False):
            self._dots[name] = ctk.CTkLabel(
                dots, text=f"● {name}", text_color=theme.TEXT_MUTED
            )
            self._dots[name].pack(side="left", padx=(0, theme.PAD))

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
        task = f"Current task: {stats.get('current_task', 'Idle')}"
        if self.lbl_task.cget("text") != task:
            self.lbl_task.configure(text=task)
        for name, text in card_values(stats, snap).items():
            label = self._cards[name]
            if label.cget("text") != text:
                label.configure(text=text)
        arduino = self.ctx.hardware.backend is not None
        for name, ok in health(snap, arduino, self.ctx.bot_running()):
            self._dots[name].configure(text_color=dot_color(ok))
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
