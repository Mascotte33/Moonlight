"""Settings tab — bot lifecycle controls and core configuration (T39).

Start / Pause (stop) / Kill drive the bot loop thread (via the controller);
the tick rate, window target and input mode fields edit the live
:class:`~moonlight.gui.config_bridge.BotConfig` (thread-safe store).
The config dropdown picks a named config in ``configs/`` and Save as copies
the picked one under a new name (T0.F1.K3).
"""

from __future__ import annotations

from tkinter import messagebox
from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui import theme
from moonlight.gui.loop_factory import (
    config_names,
    load_real_config,
    picked_config,
    save_config_as,
)
from moonlight.gui.tabs.base import Tab, section
from moonlight.setup.config import ConfigError

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState
    from moonlight.gui.config_bridge import BotConfig

__all__ = ["SettingsTab"]


def _parse_float(text: str) -> float | None:
    """Parse a float from a field, returning None on bad input."""
    try:
        return float(text.strip())
    except ValueError:
        return None


def _parse_int(text: str) -> int | None:
    """Parse an int from a field, returning None on bad input."""
    try:
        return int(text.strip())
    except ValueError:
        return None


class SettingsTab(Tab):
    """Bot lifecycle (start/pause/kill) + core config (tick rate, window,
    input mode, module toggles) with save/load to JSON."""

    title = "Settings"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)

        # -- lifecycle controls -------------------------------------------
        ctrl_box = section(parent, "Bot control")
        ctrl_box.pack(fill="x", padx=10, pady=(10, 4))
        self.btn_start = ctk.CTkButton(
            ctrl_box,
            text="Start",
            width=110,
            fg_color=theme.LIFE,
            hover_color=theme.LIFE_HOVER,
            text_color=theme.NIGHT,
        )
        self.btn_start.pack(side="left", padx=(10, 4), pady=8)
        self.btn_pause = ctk.CTkButton(
            ctrl_box,
            text="Pause (stop)",
            width=110,
            fg_color=theme.AMBER,
            hover_color=theme.AMBER_HOVER,
            text_color=theme.NIGHT,
        )
        self.btn_pause.pack(side="left", padx=4, pady=8)
        self.btn_kill = ctk.CTkButton(
            ctrl_box,
            text="Kill (F12)",
            width=110,
            fg_color=theme.BLOOD,
            hover_color=theme.BLOOD_HOVER,
            text_color=theme.NIGHT,
        )
        self.btn_kill.pack(side="left", padx=4, pady=8)
        self.lbl_status = ctk.CTkLabel(ctrl_box, text="stopped", anchor="w")
        self.lbl_status.pack(side="left", padx=12, pady=8)

        # -- core config ----------------------------------------------------
        cfg_box = section(parent, "Configuration")
        cfg_box.pack(fill="both", expand=True, padx=10, pady=4)
        cfg_grid = ctk.CTkFrame(cfg_box, fg_color="transparent")
        cfg_grid.pack(fill="both", expand=True)
        cfg_grid.grid_columnconfigure(1, weight=1)

        self.var_tick = ctk.StringVar()
        self.var_window = ctk.StringVar()
        self.var_mode = ctk.StringVar(value="arduino")
        self.var_port = ctk.StringVar()
        self.var_baud = ctk.StringVar()
        self.var_healing = ctk.BooleanVar(value=True)
        self.var_cavebot = ctk.BooleanVar(value=False)
        self.var_loot = ctk.BooleanVar(value=False)
        self.var_overlay = ctk.BooleanVar(value=True)

        self._row(cfg_grid, 0, "Tick rate (Hz)")
        self.ent_tick = ctk.CTkEntry(cfg_grid, textvariable=self.var_tick, width=120)
        self.ent_tick.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        self._row(cfg_grid, 1, "Window title")
        self.ent_window = ctk.CTkEntry(cfg_grid, textvariable=self.var_window, width=120)
        self.ent_window.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        self._row(cfg_grid, 2, "Input mode")
        self.cmb_mode = ctk.CTkComboBox(
            cfg_grid, values=["arduino", "software"], variable=self.var_mode, width=120,
            state="readonly",
        )
        self.cmb_mode.grid(row=2, column=1, sticky="w", padx=8, pady=4)
        self._row(cfg_grid, 3, "Serial port")
        self.ent_port = ctk.CTkEntry(cfg_grid, textvariable=self.var_port, width=120)
        self.ent_port.grid(row=3, column=1, sticky="w", padx=8, pady=4)
        self._row(cfg_grid, 4, "Baudrate")
        self.ent_baud = ctk.CTkEntry(cfg_grid, textvariable=self.var_baud, width=120)
        self.ent_baud.grid(row=4, column=1, sticky="w", padx=8, pady=4)

        self.chk_healing = ctk.CTkCheckBox(
            cfg_grid, text="Healing module", variable=self.var_healing
        )
        self.chk_healing.grid(row=5, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        self.chk_cavebot = ctk.CTkCheckBox(
            cfg_grid, text="Cavebot module (T22)", variable=self.var_cavebot
        )
        self.chk_cavebot.grid(row=6, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        self.chk_loot = ctk.CTkCheckBox(cfg_grid, text="Loot module (T23)", variable=self.var_loot)
        self.chk_loot.grid(row=7, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        self.chk_overlay = ctk.CTkCheckBox(cfg_grid, text="Overlay", variable=self.var_overlay)
        self.chk_overlay.grid(row=8, column=0, columnspan=2, sticky="w", padx=8, pady=2)

        # -- named configs --------------------------------------------------
        io_box = section(parent, "Config")
        io_box.pack(fill="x", padx=10, pady=(4, 10))
        self.menu_config = ctk.CTkOptionMenu(
            io_box,
            values=config_names() or [picked_config().stem],
            command=self._on_pick,
            fg_color=theme.RAISED,
            button_color=theme.RAISED,
            button_hover_color=theme.HOVER,
        )
        self.menu_config.set(picked_config().stem)
        self.menu_config.pack(side="left", padx=(10, 4), pady=8)
        self.btn_save_as = ctk.CTkButton(io_box, text="Save as", width=110)
        self.btn_save_as.pack(side="left", padx=4, pady=8)
        self.lbl_io = ctk.CTkLabel(io_box, text="", anchor="w")
        self.lbl_io.pack(side="left", padx=12, pady=8)
        self._bind_buttons()
        self._load_into_fields(self.ctx.active_config())

    def _row(self, box: ctk.CTkFrame, row: int, label: str) -> None:
        ctk.CTkLabel(box, text=label, anchor="w").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )

    def _bind_buttons(self) -> None:
        self.btn_start.configure(command=self._on_start)
        self.btn_pause.configure(command=self._on_pause)
        self.btn_kill.configure(command=self._on_kill)
        self.btn_save_as.configure(command=self._on_save_as)

    # -- field <-> config helpers ------------------------------------------

    def _load_into_fields(self, cfg: BotConfig) -> None:
        """Populate the fields from a config snapshot (display helper)."""
        self.var_tick.set(str(cfg.tick_rate))
        self.var_window.set(cfg.window_title)
        self.var_mode.set(cfg.input_mode)
        self.var_port.set(cfg.serial_port or "")
        self.var_baud.set(str(cfg.serial_baudrate))
        self.var_healing.set(cfg.modules.healing)
        self.var_cavebot.set(cfg.modules.cavebot)
        self.var_loot.set(cfg.modules.loot)
        self.var_overlay.set(cfg.modules.overlay)

    def _apply_fields(self, cfg: BotConfig) -> None:
        """Write field values into *cfg* (a working copy). Raises ValueError."""
        tick = _parse_float(self.var_tick.get())
        if tick is None or not (1.0 <= tick <= 100.0):
            raise ValueError("tick rate must be a number in [1, 100]")
        window = self.var_window.get().strip()
        if not window:
            raise ValueError("window title must not be empty")
        baud = _parse_int(self.var_baud.get())
        if baud is None or baud < 300:
            raise ValueError("baudrate must be a number >= 300")
        cfg.tick_rate = tick
        cfg.window_title = window
        cfg.input_mode = self.var_mode.get()
        cfg.serial_port = self.var_port.get().strip() or None
        cfg.serial_baudrate = baud
        cfg.modules.healing = bool(self.var_healing.get())
        cfg.modules.cavebot = bool(self.var_cavebot.get())
        cfg.modules.loot = bool(self.var_loot.get())
        cfg.modules.overlay = bool(self.var_overlay.get())

    # -- lifecycle commands -------------------------------------------------

    def _on_start(self) -> None:
        ctrl = self.ctx.controller
        if ctrl is None:
            self.lbl_status.configure(text="display only (--no-bot)")
            return
        try:
            cfg = self.ctx.active_config()
            self._apply_fields(cfg)
            # Map the GUI config onto the loop's real config object.
            ctrl.loop.config = cfg.to_loop_config()
        except ValueError as exc:
            self.lbl_status.configure(text=f"invalid config: {exc}")
            return
        try:
            ctrl.start()
        except RuntimeError as exc:
            self.lbl_status.configure(text=str(exc))

    def _on_pause(self) -> None:
        ctrl = self.ctx.controller
        if ctrl is None:
            self.lbl_status.configure(text="display only (--no-bot)")
            return
        ctrl.stop()
        self.lbl_status.configure(text="paused")

    def _on_kill(self) -> None:
        ctrl = self.ctx.controller
        if ctrl is None:
            self.lbl_status.configure(text="display only (--no-bot)")
            return
        ctrl.kill()
        self.lbl_status.configure(text="killed (F12)")

    # -- named configs -------------------------------------------------------

    def _on_pick(self, name: str) -> None:
        self.ctx.pick_config(name)
        self.lbl_io.configure(text=f"picked {name}")

    def _on_save_as(self) -> None:
        name = ctk.CTkInputDialog(text="Config name:", title="Save as").get_input()
        if not name:
            return
        cfg = load_real_config()
        try:
            try:
                save_config_as(cfg, name)
            except FileExistsError:
                if not messagebox.askyesno("Save as", f"{name} exists. Overwrite it?"):
                    return
                save_config_as(cfg, name, overwrite=True)
        except ConfigError as exc:
            self.lbl_io.configure(text=str(exc))
            return
        self.menu_config.configure(values=config_names())
        self.menu_config.set(name)
        self._on_pick(name)
        self.lbl_io.configure(text=f"saved {name}")

    # -- per-poll status ------------------------------------------------------

    def on_poll(self, snap: WorldState) -> None:
        ctrl = self.ctx.controller
        if ctrl is None:
            status = "display only (--no-bot)"
        elif ctrl.is_running:
            status = f"running (state: {ctrl.loop.current_state.value})"
        else:
            status = "stopped"
        self.lbl_status.configure(text=status)
