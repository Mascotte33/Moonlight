"""Diagnostics tab: subsystem health, performance and the event log tail.

The Health card (T10.F1.K1) shows the same dots as the Dashboard, each with
its last value. The module switches live in Settings.
"""

from __future__ import annotations

import time
from collections import deque

import customtkinter as ctk

from moonlight.bot.state import WorldState
from moonlight.gui import theme
from moonlight.gui.tabs.base import Tab, dot_color, health, section, stat_label, stat_num

__all__ = ["DiagnosticsTab"]


class DiagnosticsTab(Tab):
    """Subsystem health, tick rates and the event log tail."""

    title = "Diagnostics"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        perf = section(parent, "Performance")
        perf.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 6))
        perf_grid = ctk.CTkFrame(perf, fg_color="transparent")
        perf_grid.pack(fill="both", expand=True)
        perf_grid.grid_columnconfigure((0, 1), weight=1)
        self.lbl_poll_rate = stat_label(perf_grid, "GUI poll rate")
        self.lbl_poll_rate.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        self.lbl_state_age = stat_label(perf_grid, "State age")
        self.lbl_state_age.grid(row=0, column=1, sticky="ew", padx=10, pady=(8, 2))
        self.lbl_tick_rate = stat_label(perf_grid, "Loop tick rate")
        self.lbl_tick_rate.grid(row=1, column=0, sticky="ew", padx=10)
        self.lbl_tick_ms = stat_label(perf_grid, "Avg / max tick")
        self.lbl_tick_ms.grid(row=1, column=1, sticky="ew", padx=10)

        box = section(parent, "Health")
        box.grid(row=1, column=0, sticky="nsew", padx=10, pady=6)
        self._dots: dict[str, ctk.CTkLabel] = {}
        for name, _ in health(WorldState(), False, False):
            self._dots[name] = ctk.CTkLabel(
                box, text=f"● {name}", anchor="w", text_color=theme.TEXT_MUTED
            )
            self._dots[name].pack(fill="x", padx=theme.PAD)
        ctk.CTkFrame(box, height=theme.UNIT, fg_color="transparent").pack()

        logsec = section(parent, "Event log (tail)")
        logsec.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        logsec.grid_columnconfigure(0, weight=1)
        self.log_view = ctk.CTkTextbox(logsec, height=140, wrap="none")
        self.log_view.pack(fill="both", expand=True, padx=10, pady=(6, 4))
        self.log_view.configure(state="disabled")
        ctk.CTkButton(
            logsec, text="Reset loop stats", command=self._reset_stats
        ).pack(anchor="e", padx=10, pady=(0, 8))
        self._log_text: str = ""
        self._poll_times: deque[float] = deque(maxlen=200)

    # -- actions -----------------------------------------------------------

    def _reset_stats(self) -> None:
        ctrl = self.ctx.controller
        if ctrl is not None:
            try:
                ctrl.loop.reset_stats()
            except Exception:  # noqa: BLE001 - display only
                pass

    # -- helpers -----------------------------------------------------------

    def _loop_stats(self) -> dict[str, object]:
        ctrl = self.ctx.controller
        if ctrl is None:
            return {}
        try:
            return ctrl.loop.get_stats()
        except Exception:  # noqa: BLE001 - display only
            return {}

    # -- live updates ------------------------------------------------------

    def _update_health(self, snap: WorldState) -> None:
        hw = self.ctx.hardware
        pos = snap.position
        port = hw.port or "auto"
        values = {
            "Screen": "found" if snap.screen_ok else "missing",
            "Vitals": f"HP {snap.hp_percent:.0f}%  MP {snap.mp_percent:.0f}%",
            "Minimap": f"pos {pos.x}, {pos.y}, {pos.z}",
            "Arduino": f"{port} connected" if hw.backend is not None else "not connected",
            "Bot": "running" if self.ctx.bot_running() else "stopped",
        }
        for name, ok in health(snap, hw.backend is not None, self.ctx.bot_running()):
            text = f"● {name}: {values[name]}"
            lbl = self._dots[name]
            if lbl.cget("text") != text:
                lbl.configure(text=text, text_color=dot_color(ok))

    def on_poll(self, snap: WorldState) -> None:
        now = time.monotonic()
        self._poll_times.append(now)
        while self._poll_times and now - self._poll_times[0] > 2.0:
            self._poll_times.popleft()
        if len(self._poll_times) > 2:
            window = now - self._poll_times[0]
            hz = (len(self._poll_times) - 1) / window if window > 0 else 0.0
            self.lbl_poll_rate.configure(text=f"GUI poll rate: {hz:.1f} Hz")

        self._update_health(snap)
        if snap.last_update > 0:
            age_ms = (now - snap.last_update) * 1000.0
            self.lbl_state_age.configure(text=f"State age: {age_ms:.0f} ms")

        stats = self._loop_stats()
        if stats:
            ticks = int(stat_num(stats, "tick_count"))
            uptime = stat_num(stats, "uptime_seconds")
            avg_ms = stat_num(stats, "avg_tick_time_ms")
            max_ms = stat_num(stats, "max_tick_time_ms")
            rate = ticks / uptime if uptime > 0 else 0.0
            self.lbl_tick_rate.configure(text=f"Loop tick rate: {rate:.1f} Hz")
            self.lbl_tick_ms.configure(
                text=f"Avg / max tick: {avg_ms} / {max_ms} ms"
            )

        text = self.ctx.log.text(40)
        if text != self._log_text:
            self._log_text = text
            self.log_view.configure(state="normal")
            self.log_view.delete("1.0", "end")
            self.log_view.insert("1.0", text)
            self.log_view.configure(state="disabled")
