"""Diagnostics tab: subsystem health, performance and the event log tail.

The Health card (T10.F1.K1) shows the same dots as the Dashboard, each with
its last value. The module switches live in Settings.

"Show what the bot sees" opens a window with the game grid, a red box on
every creature the health-bar scan finds and an orange box on the square
each is predicted to step to. It scans about 5 times a second only while
open, and never sends input.
"""

from __future__ import annotations

import time
import tkinter as tk
from collections import deque

import customtkinter as ctk
import cv2
import numpy as np

from moonlight.core.state import WorldState
from moonlight.gui import theme
from moonlight.gui.loop_factory import load_profile, load_real_config
from moonlight.gui.settings_tab import plain_reason
from moonlight.gui.state_bridge import LogFeed
from moonlight.gui.tab_base import Tab, dot_color, health, section, stat_label, stat_num
from moonlight.perception.creatures import find_creatures, predict

__all__ = ["DiagnosticsTab", "recent_errors", "seen_view"]


def recent_errors(log: LogFeed) -> str:
    """The last 20 warning and error lines, newest first, or "No errors"."""
    lines = log.text(20, levels=("warn", "error")).splitlines()
    return "\n".join(reversed(lines)) or "No errors"


#: Debug window refresh, ms (about 5 scans a second).
SEES_MS = 200
#: Debug window width, px.
SEES_W = 720


def seen_view(
    frame: np.ndarray,
    grid: tuple[int, int, int, int],
    creatures: list[tuple[int, int]],
    predicted: set[tuple[int, int]],
) -> np.ndarray:
    """The game grid of *frame* (BGR) with tile lines and the scan's boxes.

    # doc: docs/prs/keys-walking.md#creature-scan
    """
    gx, gy, gw, gh = grid
    view = frame[gy : gy + gh, gx : gx + gw].copy()
    tw, th = gw / 15, gh / 11
    for col in range(16):
        x = min(round(col * tw), gw - 1)
        cv2.line(view, (x, 0), (x, gh - 1), (90, 90, 90), 1)
    for row in range(12):
        y = min(round(row * th), gh - 1)
        cv2.line(view, (0, y), (gw - 1, y), (90, 90, 90), 1)
    for slots, color in ((predicted, (62, 155, 201)), (creatures, (81, 93, 229))):
        for col, row in slots:
            a = (round(col * tw) + 2, round(row * th) + 2)
            b = (round((col + 1) * tw) - 3, round((row + 1) * th) - 3)
            cv2.rectangle(view, a, b, color, 3)
    return view


class DiagnosticsTab(Tab):
    """Subsystem health, tick rates and the event log tail."""

    title = "Diagnostics"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(3, weight=1)

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

        errs = section(parent, "Recent errors")
        errs.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 6))
        self.lbl_errors = ctk.CTkLabel(
            errs, text="No errors", anchor="w", justify="left", text_color=theme.BLOOD
        )
        self.lbl_errors.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))

        logsec = section(parent, "Event log (tail)")
        logsec.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
        logsec.grid_columnconfigure(0, weight=1)
        self.log_view = ctk.CTkTextbox(logsec, height=140, wrap="none")
        self.log_view.pack(fill="both", expand=True, padx=10, pady=(6, 4))
        self.log_view.configure(state="disabled")
        ctk.CTkButton(
            logsec, text="Reset loop stats", command=self._reset_stats
        ).pack(anchor="e", padx=10, pady=(0, 8))

        sees = section(parent, "What the bot sees")
        sees.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 10))
        ctk.CTkButton(sees, text="Show what the bot sees", command=self._show_sees).pack(
            anchor="w", padx=theme.PAD
        )
        self.lbl_sees = ctk.CTkLabel(sees, text="", anchor="w", text_color=theme.AMBER)
        self.lbl_sees.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))
        self._sees: ctk.CTkToplevel | None = None
        self._root = parent
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

    def _show_sees(self) -> None:
        """Open the live creature-scan window (one at a time)."""
        if self._sees is not None and self._sees.winfo_exists():
            self._sees.focus()
            return
        from moonlight.calibration.tab import _photo
        from moonlight.perception.capture import CaptureError, ScreenCapture

        try:
            cfg = load_real_config()
            grid = load_profile(cfg.profile, cfg.profiles_dir).get_region("game_grid").to_tuple
            cap = ScreenCapture(window_title=cfg.window_title)
        except Exception as exc:  # any setup failure is shown, never raised in Tk
            self.lbl_sees.configure(text=plain_reason(str(exc)))
            return
        win = self._sees = ctk.CTkToplevel(self._root)
        win.title("What the bot sees")
        label = tk.Label(win, bg=theme.NIGHT, fg=theme.TEXT, text="Looking for Tibia...")
        label.pack()
        scale = SEES_W / grid[2]
        prev: list[tuple[int, int]] = []

        def close() -> None:
            cap.close()
            win.destroy()

        def tick() -> None:
            if not win.winfo_exists():
                return
            try:
                frame = np.ascontiguousarray(cap.get_frame([grid])[:, :, ::-1])  # RGB -> BGR
            except CaptureError as exc:
                label.configure(image="", text=str(exc))
                win.after(1000, tick)
                return
            slots = find_creatures(frame, grid)
            photo = _photo(seen_view(frame, grid, slots, predict(slots, prev)), scale)
            label.configure(image=photo, text="")
            label.image = photo  # type: ignore[attr-defined]  # keep it alive
            prev[:] = slots
            win.after(SEES_MS, tick)

        win.protocol("WM_DELETE_WINDOW", close)
        win.after(0, tick)

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

        errors = recent_errors(self.ctx.log)
        if self.lbl_errors.cget("text") != errors:
            self.lbl_errors.configure(text=errors)
        text = self.ctx.log.text(40)
        if text != self._log_text:
            self._log_text = text
            self.log_view.configure(state="normal")
            self.log_view.delete("1.0", "end")
            self.log_view.insert("1.0", text)
            self.log_view.configure(state="disabled")
