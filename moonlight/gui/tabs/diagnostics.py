"""Diagnostics tab (T39) — performance, module toggles, event log tail.

Shows the GUI poll rate, state freshness, loop tick rate and tick-time
percentiles, capture latency, the decision-module toggles (live-wired to
the config store), and the tail of the shared :class:`LogFeed` event log.
"""

from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui.tabs.base import Tab, section, stat_label, stat_num

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.state import WorldState

__all__ = ["DiagnosticsTab"]

_MODULE_NAMES: tuple[str, ...] = ("healing", "cavebot", "loot", "overlay")


class DiagnosticsTab(Tab):
    """FPS/tick rates, capture latency, module toggles, event log tail."""

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

        mods = section(parent, "Decision modules (live)")
        mods.grid(row=1, column=0, sticky="nsew", padx=10, pady=6)
        mods.grid_columnconfigure(0, weight=1)
        for name in _MODULE_NAMES:
            initial = bool(getattr(self.ctx.active_config().modules, name))
            var = ctk.BooleanVar(value=initial)
            ctk.CTkCheckBox(
                mods,
                text=name,
                variable=var,
                command=lambda n=name, v=var: self._set_module(n, v),
            ).pack(anchor="w", padx=10, pady=3)

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

    def _set_module(self, name: str, var: ctk.BooleanVar) -> None:
        self.ctx.config_store.update(
            lambda cfg: setattr(cfg.modules, name, bool(var.get()))
        )

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

    def on_poll(self, snap: WorldState) -> None:
        now = time.monotonic()
        self._poll_times.append(now)
        while self._poll_times and now - self._poll_times[0] > 2.0:
            self._poll_times.popleft()
        if len(self._poll_times) > 2:
            window = now - self._poll_times[0]
            hz = (len(self._poll_times) - 1) / window if window > 0 else 0.0
            self.lbl_poll_rate.configure(text=f"GUI poll rate: {hz:.1f} Hz")

        if snap.last_update > 0:
            age_ms = (time.time() - snap.last_update) * 1000.0
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
