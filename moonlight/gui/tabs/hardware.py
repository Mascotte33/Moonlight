"""Hardware tab (T39) — Arduino input backend controls.

Connect / disconnect the serial Arduino, verify the PONG handshake,
release all held buttons, and show live connection status. The backend
lives on the shared :class:`moonlight.gui.state_bridge.HardwareController`
(``ctx.hardware``) so the bot loop can use the same device.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui.tabs.base import Tab, section, stat_label

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState

__all__ = ["HardwareTab"]


class HardwareTab(Tab):
    """Arduino backend status and controls (list, connect, ping, release)."""

    title = "Hardware"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)

        self.lbl_backend = stat_label(parent, "Backend")
        self.lbl_backend.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 2))
        self.lbl_port = stat_label(parent, "Port")
        self.lbl_port.grid(row=1, column=0, sticky="ew", padx=10)
        self.lbl_pong = stat_label(parent, "PONG")
        self.lbl_pong.grid(row=2, column=0, sticky="ew", padx=10)
        self.lbl_error = stat_label(parent, "Error")
        self.lbl_error.grid(row=3, column=0, sticky="ew", padx=10)
        self._last_pong: str = "--"
        self._msg: str = ""

        box = section(parent, "Arduino backend")
        box.grid(row=4, column=0, sticky="nsew", padx=10, pady=(10, 10))
        box_grid = ctk.CTkFrame(box, fg_color="transparent")
        box_grid.pack(fill="both", expand=True)
        box_grid.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            box_grid, text="Serial port (blank = auto-detect)"
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 0))
        self.var_port = ctk.StringVar(value="")
        ctk.CTkEntry(
            box_grid, textvariable=self.var_port, placeholder_text="e.g. COM3"
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        ctk.CTkLabel(box_grid, text="Baud rate").grid(row=2, column=0, sticky="w", padx=10)
        self.var_baud = ctk.StringVar(value="115200")
        ctk.CTkEntry(box_grid, textvariable=self.var_baud).grid(
            row=2, column=1, sticky="ew", padx=10
        )

        btns = ctk.CTkFrame(box_grid, fg_color="transparent")
        btns.grid(row=3, column=0, columnspan=2, sticky="ew", padx=6, pady=(10, 4))
        for col, (text, cmd) in enumerate(
            (
                ("List ports", self._list_ports),
                ("Connect", self._connect),
                ("Ping (PONG)", self._ping),
                ("Release all", self._release_all),
                ("Disconnect", self._disconnect),
            )
        ):
            ctk.CTkButton(btns, text=text, command=cmd).grid(
                row=0, column=col, padx=4, sticky="ew"
            )
            btns.grid_columnconfigure(col, weight=1)

        # Simulation toggle (F-13): explicitly opt into FakeInput so the GUI
        # stays usable on a machine without the board. Off by default — a
        # missing Arduino is then a hard error (no silent fallback).
        self.var_simulation = ctk.BooleanVar(
            value=self.ctx.active_config().simulation
        )
        ctk.CTkCheckBox(
            box_grid,
            text="Simulation mode (no Arduino — FakeInput)",
            variable=self.var_simulation,
            command=self._on_simulation_toggle,
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 0))

        self.lbl_ports = ctk.CTkLabel(
            box_grid, text="", anchor="w", justify="left", font=ctk.CTkFont(size=11)
        )
        self.lbl_ports.grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10)
        )

    # -- actions -----------------------------------------------------------

    def _list_ports(self) -> None:
        try:
            ports = self.ctx.hardware.list_ports()
        except Exception as exc:  # noqa: BLE001 - display only
            self._msg = f"port list failed: {exc!r}"
            ports = []
        if ports:
            self.lbl_ports.configure(
                text="\n".join(
                    f"{p['device']}  —  {p['description'] or 'unknown device'}"
                    for p in ports[:8]
                )
            )
            self._msg = ""
        elif not self._msg:
            self._msg = "no serial ports found"
        self._refresh()

    def _connect(self) -> None:
        port = self.var_port.get().strip() or None
        try:
            baud = int(self.var_baud.get() or "115200")
        except ValueError:
            baud = 115200

        def worker() -> None:
            try:
                backend = self.ctx.hardware.connect(port=port, baudrate=baud)
                name = getattr(backend, "connected_port", None)
                label = name() if callable(name) else type(backend).__name__
                self._msg = f"connected: {label}"
            except Exception as exc:  # noqa: BLE001 - show on tab
                self._msg = f"connect failed: {exc!r}"
            self._refresh()

        threading.Thread(
            target=worker, daemon=True, name="gui-hw-connect"
        ).start()

    def _ping(self) -> None:
        try:
            pong = self.ctx.hardware.ping()
        except Exception as exc:  # noqa: BLE001 - show on tab
            self._msg = f"ping failed: {exc!r}"
        else:
            if pong is None:
                self._msg = "not connected — nothing to ping"
            else:
                self._msg = (
                    "PONG received — device alive" if pong else "no PONG — unresponsive"
                )
        self._last_pong = self._msg or self._last_pong
        self._refresh()

    def _release_all(self) -> None:
        self.ctx.hardware.release_all()
        self._msg = "released all held buttons"
        self._refresh()

    def _disconnect(self) -> None:
        self.ctx.hardware.disconnect()
        self._msg = "disconnected"
        self._last_pong = "--"
        self._refresh()

    def _on_simulation_toggle(self) -> None:
        """Persist the simulation toggle to the config store (F-13).

        The checkbox has already updated ``var_simulation``; we read it back
        and persist so the next ``build_gui_loop`` picks it up.
        """
        checked = bool(self.var_simulation.get())
        self.ctx.config_store.update(
            lambda cfg: setattr(cfg, "simulation", checked)
        )

    # -- display -----------------------------------------------------------

    def _refresh(self) -> None:
        if self._msg:
            self.lbl_ports.configure(text=self._msg)

    def on_poll(self, snap: WorldState) -> None:
        hw = self.ctx.hardware
        if hw.backend is None:
            backend_text = "Backend: disconnected"
            port_text = "Port: --"
        else:
            backend = hw.backend
            alive = getattr(backend, "is_alive", None)
            alive_s = "alive" if alive else ("lost" if alive is False else "unknown")
            backend_text = f"Backend: {type(backend).__name__} ({alive_s})"
            port_text = f"Port: {hw.port or 'auto'}"
        if self.lbl_backend.cget("text") != backend_text:
            self.lbl_backend.configure(text=backend_text)
        if self.lbl_port.cget("text") != port_text:
            self.lbl_port.configure(text=port_text)
        if self.lbl_pong.cget("text") != f"PONG: {self._last_pong}":
            self.lbl_pong.configure(text=f"PONG: {self._last_pong}")
        err = f"Error: {hw.last_error or '—'}"
        if self.lbl_error.cget("text") != err:
            self.lbl_error.configure(text=err)
