"""Main app shell (T39) — the dark 10-tab customtkinter window.

Run with ``python -m moonlight.gui`` (see :mod:`moonlight.gui.__main__`).
The shell wires the shared :class:`~moonlight.gui.state_bridge.GuiContext`
(live WorldState, config store, hardware controller, log feed) into all ten
tabs and drives a Tk ``after``-based poller that refreshes every tab at
~10 Hz. The bot loop thread is started/stopped through the shared
:class:`~moonlight.gui.state_bridge.BotController` — the GUI itself is a
thin presentation layer and never reimplements bot logic.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import sys
import threading
import time
from pathlib import Path

import customtkinter as ctk

from moonlight import x11
from moonlight.calibration.tab import CalibrationTab
from moonlight.cavebot.tab import CavebotTab
from moonlight.core.state import WorldState
from moonlight.gui import theme
from moonlight.gui.dashboard_tab import DashboardTab
from moonlight.gui.diagnostics_tab import DiagnosticsTab
from moonlight.gui.loop_factory import build_gui_loop, build_position_reader, load_real_config
from moonlight.gui.settings_tab import SettingsTab
from moonlight.gui.state_bridge import (
    BotController,
    GuiContext,
    HardwareController,
    LogFeed,
    topbar_text,
)
from moonlight.gui.status_tab import StatusTab
from moonlight.gui.sysinfo import cpu_percent, cpu_times, memory_text
from moonlight.gui.tab_base import Tab, dot_color, health
from moonlight.hardware.tab import HardwareTab
from moonlight.healing.tab import HealingTab
from moonlight.targeting.tab import TargetingTab

#: Poller period in ms (10 Hz — light enough for a low-spec laptop).
POLL_MS = 100
RETRY_S = 5.0  # seconds between tries to start a bot that is down
WATCH_S = 0.2  # seconds between position reads while the bot is stopped
_VK_F12 = 0x7B
_f12_was_down = False


def f12_pressed() -> bool:
    """True once per F12 press, in any window.

    Windows won't hand F12 to ``RegisterHotKey`` (it is reserved for
    debuggers), so the poller reads the key state instead. Bit 0 means
    "pressed since the last call", which catches taps shorter than a poll.
    X11 has no such bit, so Linux catches the rising edge between polls.
    """
    global _f12_was_down
    if sys.platform == "linux":
        down = x11.vk_down(_VK_F12)
        pressed, _f12_was_down = down and not _f12_was_down, down
        return pressed
    if sys.platform != "win32":
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(_VK_F12) & 1)


SIDEBAR_WIDTH = 200

#: Top-bar state dot color per state text.
STATE_COLORS = {
    "Running": theme.LIFE,
    "Paused": theme.AMBER,
    "Waiting for game": theme.AMBER,
    "Stopped": theme.TEXT_MUTED,
}

#: The tabs in display order.
TAB_ORDER = (
    DashboardTab,
    SettingsTab,
    CalibrationTab,
    CavebotTab,
    HealingTab,
    TargetingTab,
    HardwareTab,
    StatusTab,
    DiagnosticsTab,
)

__all__ = [
    "GuiApp",
    "POLL_MS",
    "TAB_ORDER",
    "main",
]


class GuiApp:
    """customtkinter shell hosting the tabs and the state poller."""

    def __init__(
        self,
        start_bot: bool = True,
    ) -> None:
        theme.apply()

        self.state = WorldState()
        self.log = LogFeed()
        self.hardware = HardwareController(log=self.log)
        self.ctx = GuiContext(
            state=self.state,
            hardware=self.hardware,
            log=self.log,
        )
        self.ctx.on_config_change = self.apply_config

        self.root = ctk.CTk()
        self.root.title("MOONLIGHT — Tibia automation")
        self.root.geometry("1100x740")
        self.root.minsize(880, 580)

        self._build_topbar()
        self._build_bottombar()

        body = ctk.CTkFrame(self.root, fg_color="transparent", corner_radius=0)
        body.pack(fill="both", expand=True, pady=(6, 0))
        sidebar = ctk.CTkFrame(
            body, width=SIDEBAR_WIDTH, fg_color=theme.NIGHT, corner_radius=0
        )
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        ctk.CTkFrame(body, width=1, fg_color=theme.RAISED, corner_radius=0).pack(
            side="left", fill="y"
        )
        ctk.CTkLabel(
            sidebar,
            text="MOONLIGHT",
            font=ctk.CTkFont(size=theme.SIZE_HEADING, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=theme.PAD, pady=(theme.PAD, theme.GAP))
        content = ctk.CTkFrame(body, fg_color="transparent", corner_radius=0)
        content.pack(side="left", fill="both", expand=True, padx=10, pady=(0, 10))
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)

        self.tabs: dict[str, Tab] = {}
        self._pages: dict[str, ctk.CTkFrame | ctk.CTkScrollableFrame] = {}
        self._nav: dict[str, ctk.CTkButton] = {}
        for tab_cls in TAB_ORDER:
            tab = tab_cls(self.ctx)
            title = tab.title
            frame = ctk.CTkScrollableFrame if tab.scrolls else ctk.CTkFrame
            page = frame(content, fg_color="transparent")
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_columnconfigure(0, weight=1)
            page.grid_rowconfigure(0, weight=1)
            tab.build(page)
            self.tabs[title] = tab
            self._pages[title] = page
            self._nav[title] = ctk.CTkButton(
                sidebar,
                text=title,
                anchor="w",
                height=32,
                corner_radius=theme.RADIUS_CONTROL,
                font=ctk.CTkFont(size=theme.SIZE_BODY),
                command=lambda t=title: self.select_tab(t),
            )
            self._nav[title].pack(fill="x", padx=theme.UNIT * 2, pady=1)
        self.select_tab(next(iter(self.tabs)))

        #: Stop or the kill switch hold the bot down until Start.
        self._held = not start_bot
        #: Pause: the bot runs without cavebot and targeting until Start.
        self._retry_at = 0.0
        self._start_error = ""
        f12_pressed()  # drop a press from before the app opened
        if start_bot:
            self.start_bot()
        self._closing = threading.Event()
        threading.Thread(target=self._watch_position, name="position", daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(POLL_MS, self._poll)

    def select_tab(self, title: str) -> None:
        """Raise ``title``'s page and highlight its sidebar entry."""
        self._pages[title].lift()
        for name, btn in self._nav.items():
            active = name == title
            btn.configure(
                fg_color=theme.MANA if active else "transparent",
                text_color=theme.NIGHT if active else theme.TEXT_DIM,
                hover_color=theme.MANA_HOVER if active else theme.HOVER,
            )

    # -- header / bot control ----------------------------------------------

    def _build_topbar(self) -> None:
        bar = ctk.CTkFrame(self.root, corner_radius=0, fg_color=theme.PANEL)
        bar.pack(fill="x")
        ctk.CTkFrame(self.root, height=1, fg_color=theme.RAISED, corner_radius=0).pack(
            fill="x"
        )
        mono = ctk.CTkFont(family=theme.MONO, size=theme.SIZE_BODY)
        pad = theme.PAD

        def vital(name: str, color: str) -> tuple[ctk.CTkProgressBar, ctk.CTkLabel]:
            ctk.CTkLabel(bar, text=name, font=mono).pack(
                side="left", padx=(pad, theme.UNIT), pady=pad
            )
            progress = ctk.CTkProgressBar(bar, width=120, progress_color=color)
            progress.set(0)
            progress.pack(side="left")
            pct = ctk.CTkLabel(bar, text="0%", width=40, font=mono)
            pct.pack(side="left", padx=(theme.UNIT, theme.GAP))
            return progress, pct

        self.bar_hp, self.lbl_hp = vital("HP", theme.LIFE)
        self.bar_mp, self.lbl_mp = vital("MP", theme.MANA)

        buttons = (
            ("■ Stop", self.stop_bot, theme.BLOOD, theme.BLOOD_HOVER),
            ("⏸ Pause", self.pause_bot, theme.AMBER, theme.AMBER_HOVER),
            ("▶ Start", self.resume_bot, theme.LIFE, theme.LIFE_HOVER),
        )
        made: list[ctk.CTkButton] = []
        self._btn_colors: dict[ctk.CTkButton, tuple[str, str]] = {}
        for text, command, color, hover in buttons:
            btn = ctk.CTkButton(
                bar,
                text=text,
                command=command,
                width=90,
                fg_color=color,
                hover_color=hover,
                text_color=theme.NIGHT,
            )
            btn.pack(side="right", padx=(0, theme.UNIT if made else theme.GAP))
            self._btn_colors[btn] = (color, hover)
            made.append(btn)
        self.btn_stop, self.btn_pause, self.btn_start = made
        self.lbl_state = ctk.CTkLabel(bar, text="Stopped", font=mono)
        self.lbl_state.pack(side="right", padx=(theme.UNIT, pad))
        self.lbl_dot = ctk.CTkLabel(bar, text="●", text_color=theme.TEXT_MUTED)
        self.lbl_dot.pack(side="right")

    def _build_bottombar(self) -> None:
        bar = ctk.CTkFrame(self.root, corner_radius=0, fg_color=theme.PANEL)
        bar.pack(side="bottom", fill="x")
        ctk.CTkFrame(self.root, height=1, fg_color=theme.RAISED, corner_radius=0).pack(
            side="bottom", fill="x"
        )
        mono = ctk.CTkFont(family=theme.MONO, size=theme.SIZE_TITLE)
        self._dots: dict[str, ctk.CTkLabel] = {}
        for name, _ in health(WorldState(), False, False):
            self._dots[name] = ctk.CTkLabel(
                bar, text=f"● {name}", font=mono, text_color=theme.TEXT_MUTED
            )
            self._dots[name].pack(side="left", padx=(theme.PAD, 0), pady=theme.UNIT)
        self.lbl_mem = ctk.CTkLabel(bar, text="MEM —", font=mono)
        self.lbl_mem.pack(side="right", padx=theme.PAD)
        self.lbl_cpu = ctk.CTkLabel(bar, text="CPU 0%", font=mono)
        self.lbl_cpu.pack(side="right")
        self._res_t = time.monotonic()
        self._cpu = cpu_times()

    def _update_bottombar(self) -> None:
        snap = self.ctx.state.snapshot()
        for name, ok in health(snap, self.hardware.backend is not None, self.ctx.bot_running()):
            self._dots[name].configure(text_color=dot_color(ok))
        now = time.monotonic()
        if now - self._res_t >= 1.0:  # CPU/MEM refresh about once a second
            cpu = cpu_times()
            text = "—" if cpu is None or self._cpu is None else (
                f"{cpu_percent(self._cpu, cpu):.0f}%"
            )
            self.lbl_cpu.configure(text=f"CPU {text}")
            self.lbl_mem.configure(text=f"MEM {memory_text()}")
            self._res_t, self._cpu = now, cpu

    def pause_bot(self) -> None:
        """Pause: cavebot and targeting stop; refill, healing, spells and alarms go on."""
        self.ctx.paused = True
        ctrl = self.ctx.controller
        if ctrl is not None:
            ctrl.loop.paused = True
        self._update_topbar()

    def resume_bot(self) -> None:
        """Start button and F12: undo the last Pause or Stop."""
        self._held = self.ctx.paused = False
        ctrl = self.ctx.controller
        if ctrl is not None and self.ctx.bot_running():
            ctrl.loop.paused = False
            self._update_topbar()
        else:
            self.start_bot(resume=ctrl is not None)

    def apply_config(self, restart: bool = False) -> None:
        """Hand the picked config to the running bot (live apply).

        Module settings swap in place; anything else (port, window, tick
        rate, a module switched on or off, or *restart*) restarts the bot,
        which releases all keys first. A refused config is logged and the
        bot keeps going.
        """
        # doc: docs/prs/gui-calibration.md#live-apply
        ctrl = self.ctx.controller
        if ctrl is None or not self.ctx.bot_running() or ctrl.loop.reconfigure is None:
            return
        try:
            if not restart and ctrl.loop.reconfigure(load_real_config()):
                return
        except Exception as exc:  # noqa: BLE001 - keep the last good modules
            self.log.error(f"settings not applied: {exc!r}")
            return
        self.log.info("settings changed, restarting the bot")
        ctrl.stop()
        self.start_bot(resume=True)

    def start_bot(self, *, resume: bool = False) -> None:
        """Start the real bot loop through :mod:`moonlight.core.wiring` (F-13).

        Builds the loop via :func:`moonlight.gui.loop_factory.build_gui_loop`,
        which wires a real screen capture (frame provider + focus check) and
        every enabled decision module — never a bare :class:`GameLoop`
        without ``perceive`` (which would stop on the first tick). It reads only
        ``moonlight_config.json``; a missing Arduino requires its explicit
        ``simulation`` flag. There is no silent :class:`FakeInput` fallback (F-13).
        """
        self._held = False
        if self.ctx.bot_running():
            return
        self._retry_at = time.monotonic() + RETRY_S
        try:
            loop = build_gui_loop(hardware=self.hardware, state=self.state)
        except Exception as exc:  # noqa: BLE001 - report, don't crash the app
            if repr(exc) != self._start_error:  # the retry repeats it every few seconds
                self.log.error(f"could not start bot: {exc!r}")
            self._start_error = repr(exc)
            return
        self._start_error = ""
        if resume and loop.modules.cavebot is not None:
            loop.modules.cavebot.start(nearest=True)
        loop.paused = self.ctx.paused  # a restart keeps a Pause
        self.ctx.controller = BotController(loop, log=self.log)
        self.ctx.controller.start()
        self._update_topbar()

    def stop_bot(self) -> None:
        """Stop: the whole loop (healing, spells, alarms too) until Start."""
        self._held = True
        self.ctx.paused = False
        ctrl = self.ctx.controller
        if ctrl is not None:
            ctrl.stop()
        self._update_topbar()

    def _update_topbar(self) -> None:
        ctrl = self.ctx.controller
        running = self.ctx.bot_running()
        snap = self.ctx.state.snapshot()
        hp, mp, state = topbar_text(
            snap, running, ctrl.state if ctrl is not None else None, self.ctx.paused
        )
        self.bar_hp.set(min(max(snap.hp_percent / 100, 0.0), 1.0))
        self.bar_mp.set(min(max(snap.mp_percent / 100, 0.0), 1.0))
        self.lbl_hp.configure(text=hp)
        self.lbl_mp.configure(text=mp)
        self.lbl_state.configure(text=state)
        self.lbl_dot.configure(text_color=STATE_COLORS[state])
        enabled = {
            self.btn_start: not running or self.ctx.paused,
            self.btn_pause: running and not self.ctx.paused,
            self.btn_stop: running,
        }
        for btn, on in enabled.items():
            btn.configure(
                state="normal" if on else "disabled",
                **theme.button_colors(on, *self._btn_colors[btn]),
            )

    # -- poll loop -----------------------------------------------------------

    def _poll(self) -> None:
        """One poller pass: snapshot the state and refresh every tab."""
        try:
            if f12_pressed():
                self.resume_bot() if self._held or self.ctx.paused else self.pause_bot()
            self._keep_running()
            snap = self.ctx.state.snapshot()
            for tab in self.tabs.values():
                tab.on_poll(snap)
            self._update_topbar()
            self._update_bottombar()
        except Exception as exc:  # noqa: BLE001 - the poller must survive
            self.log.warn(f"poll error: {exc!r}")
        finally:
            try:
                self.root.after(POLL_MS, self._poll)
            except ctk.TclError:  # window destroyed mid-schedule
                pass

    def _keep_running(self) -> None:
        """Start the bot again when it is down and nobody stopped it."""
        ctrl = self.ctx.controller
        if ctrl is not None and ctrl.loop.kill_switch_active:
            self._held = True
        if self._held or self.ctx.bot_running() or time.monotonic() < self._retry_at:
            return
        self.start_bot(resume=ctrl is not None)

    def _watch_position(self) -> None:
        """Keep reading the position while the bot is stopped (screen only)."""
        perceive = None
        retry_at = 0.0
        while not self._closing.wait(WATCH_S):
            if self.ctx.bot_running():
                perceive = None  # the next stop starts from the bot's last position
                continue
            if perceive is None:
                if time.monotonic() < retry_at:
                    continue
                try:
                    perceive = build_position_reader(self.state)
                except Exception:  # noqa: BLE001 - no profile or window yet; try again
                    retry_at = time.monotonic() + RETRY_S
                    continue
            perceive()

    def on_close(self) -> None:
        """Window close: stop the bot and close hardware."""
        self._closing.set()
        try:
            self.stop_bot()
            self.hardware.disconnect()
        finally:
            self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m moonlight`` / ``python -m moonlight.gui``.

    ``--help`` prints usage and exits 0 without creating any window.
    """
    parser = argparse.ArgumentParser(
        prog="python -m moonlight.gui",
        description=(
            "MOONLIGHT Tibia bot GUI — dark 10-tab customtkinter control "
            "panel (dashboard, settings, cavebot, healing, targeting, "
            "spell attack, hardware, recorder, status, diagnostics)."
        ),
    )
    parser.add_argument(
        "--no-bot",
        action="store_true",
        help="start the GUI without launching the bot loop thread",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(format="%(message)s")  # warnings, as before
    logging.getLogger().handlers[0].setLevel(logging.INFO)  # debug goes to the file only
    # Keys sent or dropped and why the cavebot attacks, for diagnosing in game.
    Path("logs").mkdir(exist_ok=True)
    file = logging.FileHandler("logs/bot.log", mode="w", encoding="utf-8")
    file.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    logging.getLogger("moonlight").addHandler(file)
    logging.getLogger("moonlight").setLevel(logging.INFO)  # state changes, restarts
    logging.getLogger("moonlight.core.safety").setLevel(logging.DEBUG)
    logging.getLogger("moonlight.cavebot.cavebot").setLevel(logging.DEBUG)
    # The bot thread shares the GIL with the GUI. At the default 5 ms switch
    # interval a busy GUI (a row redraw) made a numpy/cv2 tick 6.6x slower;
    # at 0.5 ms 1.2x (measured).
    sys.setswitchinterval(0.0005)
    app = GuiApp(start_bot=not args.no_bot)
    app.root.mainloop()
    return 0
