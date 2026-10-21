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
from pathlib import Path

import customtkinter as ctk

from moonlight.bot.state import WorldState
from moonlight.gui import config_bridge
from moonlight.gui.loop_factory import build_gui_loop
from moonlight.gui.state_bridge import (
    BotController,
    GuiContext,
    HardwareController,
    LogFeed,
)
from moonlight.gui.tabs.base import Tab
from moonlight.gui.tabs.cavebot import CavebotTab
from moonlight.gui.tabs.dashboard import DashboardTab
from moonlight.gui.tabs.diagnostics import DiagnosticsTab
from moonlight.gui.tabs.hardware import HardwareTab
from moonlight.gui.tabs.healing import HealingTab
from moonlight.gui.tabs.recorder import RecorderTab
from moonlight.gui.tabs.settings import SettingsTab
from moonlight.gui.tabs.spell_attack import SpellAttackTab
from moonlight.gui.tabs.status import StatusTab
from moonlight.gui.tabs.targeting import TargetingTab

#: Poller period in ms (10 Hz — light enough for a low-spec laptop).
POLL_MS = 100

#: The ten tabs in display order.
TAB_ORDER = (
    DashboardTab,
    SettingsTab,
    CavebotTab,
    HealingTab,
    TargetingTab,
    SpellAttackTab,
    HardwareTab,
    RecorderTab,
    StatusTab,
    DiagnosticsTab,
)

__all__ = ["GuiApp", "POLL_MS", "TAB_ORDER", "main"]


class GuiApp:
    """customtkinter shell hosting the ten tabs and the state poller."""

    def __init__(
        self,
        config_path: Path | str | None = None,
        start_bot: bool = True,
    ) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.config_path = (
            Path(config_path)
            if config_path is not None
            else config_bridge.default_config_path()
        )
        self.state = WorldState()
        self.log = LogFeed()
        self.config_store = config_bridge.ConfigStore(None, self.config_path)
        self.hardware = HardwareController(log=self.log)
        self.ctx = GuiContext(
            state=self.state,
            config_store=self.config_store,
            hardware=self.hardware,
            log=self.log,
        )
        self.ctx.profile_name = self.config_store.config.profile_name

        self.root = ctk.CTk()
        self.root.title("MOONLIGHT — Tibia automation")
        self.root.geometry("1100x740")
        self.root.minsize(880, 580)

        self._build_header()

        self.tabview = ctk.CTkTabview(self.root)
        self.tabview.pack(fill="both", expand=True, padx=10, pady=(6, 10))
        self.tabs: dict[str, Tab] = {}
        for tab_cls in TAB_ORDER:
            tab = tab_cls(self.ctx)
            frame = self.tabview.add(tab.title)
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_rowconfigure(0, weight=1)
            tab.build(frame)
            self.tabs[tab.title] = tab

        if start_bot:
            self.start_bot()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(POLL_MS, self._poll)

    # -- header / bot control ----------------------------------------------

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self.root, corner_radius=0, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(10, 0))
        ctk.CTkLabel(
            header,
            text="MOONLIGHT",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(side="left", padx=6)
        self.lbl_bot = ctk.CTkLabel(
            header, text="bot: stopped", font=ctk.CTkFont(size=12)
        )
        self.lbl_bot.pack(side="left", padx=12)
        self.btn_stop = ctk.CTkButton(
            header,
            text="Stop bot",
            command=self.stop_bot,
            width=110,
            fg_color="#c0392b",
            hover_color="#e74c3c",
            state="disabled",
        )
        self.btn_stop.pack(side="right", padx=(6, 6))
        self.btn_start = ctk.CTkButton(
            header, text="Start bot", command=self.start_bot, width=110
        )
        self.btn_start.pack(side="right")

    def start_bot(self) -> None:
        """Start the real bot loop through :mod:`moonlight.bot.wiring` (F-13).

        Builds the loop via :func:`moonlight.gui.loop_factory.build_gui_loop`,
        which wires a real screen capture (frame provider + focus check) and
        every enabled decision module — never a bare :class:`GameLoop`
        without ``perceive`` (which would stop on the first tick). A missing
        Arduino requires the explicit *simulation* toggle (Hardware tab);
        there is no silent :class:`FakeInput` fallback (F-13).
        """
        if self.ctx.bot_running():
            return
        cfg = self.ctx.active_config()
        try:
            loop = build_gui_loop(
                cfg,
                hardware=self.hardware,
                state=self.state,
                config_path=self.config_path,
            )
        except Exception as exc:  # noqa: BLE001 - report, don't crash the app
            self.log.error(f"could not start bot: {exc!r}")
            return
        self.ctx.controller = BotController(loop, log=self.log)
        self.ctx.controller.start()
        self._update_header()

    def stop_bot(self) -> None:
        """Stop the bot loop thread (idempotent)."""
        ctrl = self.ctx.controller
        if ctrl is not None:
            ctrl.stop()
        self._update_header()

    def _update_header(self) -> None:
        running = self.ctx.bot_running()
        self.lbl_bot.configure(
            text="bot: running" if running else "bot: stopped"
        )
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")

    # -- poll loop -----------------------------------------------------------

    def _poll(self) -> None:
        """One poller pass: snapshot the state and refresh every tab."""
        try:
            snap = self.ctx.state.snapshot()
            for tab in self.tabs.values():
                tab.on_poll(snap)
            self._update_header()
        except Exception as exc:  # noqa: BLE001 - the poller must survive
            self.log.warn(f"poll error: {exc!r}")
        finally:
            try:
                self.root.after(POLL_MS, self._poll)
            except ctk.TclError:  # window destroyed mid-schedule
                pass

    def on_close(self) -> None:
        """Window close: stop the bot, close hardware, persist config."""
        try:
            self.stop_bot()
            self.hardware.disconnect()
            self.config_store.save()
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
        "--config",
        type=Path,
        default=None,
        help="path to config.json (default: <repo>/data/config.json)",
    )
    parser.add_argument(
        "--no-bot",
        action="store_true",
        help="start the GUI without launching the bot loop thread",
    )
    args = parser.parse_args(argv)

    app = GuiApp(config_path=args.config, start_bot=not args.no_bot)
    app.root.mainloop()
    return 0
