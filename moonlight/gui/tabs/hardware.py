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

from moonlight.gui import theme
from moonlight.gui.tabs.base import Option, Tab, section, stat_label

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState

__all__ = ["OPTIONS", "HardwareTab"]

OPTIONS = [
    Option("Serial port (blank = auto-detect)", "serial_port", "entry"),
    Option("Baud rate", "serial_baudrate", "entry", min=1),
    Option("Log every Arduino command", "log_commands", "switch"),
    Option("Simulation", "simulation", "switch"),
]


class HardwareTab(Tab):
    """Arduino backend status and controls (list, connect, ping, release)."""

    title = "Hardware"
    options = OPTIONS

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

        options = section(parent, "Connection")
        options.grid(row=4, column=0, sticky="ew", padx=10, pady=(10, 0))
        self.build_options(options)
        detect = ctk.CTkFrame(options, fg_color="transparent")
        detect.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        self.menu_ports = ctk.CTkOptionMenu(
            detect,
            values=["Click Detect"],
            command=self._writers["serial_port"],
            fg_color=theme.RAISED,
            button_color=theme.RAISED,
            button_hover_color=theme.HOVER,
        )
        self.menu_ports.pack(side="right")
        ctk.CTkButton(
            detect,
            text="Detect",
            width=80,
            command=self._detect,
            fg_color=theme.RAISED,
            hover_color=theme.HOVER,
        ).pack(side="right", padx=theme.UNIT)
        ctk.CTkLabel(
            options,
            text="No Arduino. Actions are only logged.",
            anchor="w",
            font=ctk.CTkFont(size=theme.SIZE_TITLE),
            text_color=theme.TEXT_MUTED,
        ).pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))

        box = section(parent, "Arduino backend")
        box.grid(row=5, column=0, sticky="nsew", padx=10, pady=(10, 10))
        box_grid = ctk.CTkFrame(box, fg_color="transparent")
        box_grid.pack(fill="both", expand=True)
        box_grid.grid_columnconfigure(1, weight=1)

        btns = ctk.CTkFrame(box_grid, fg_color="transparent")
        btns.grid(row=3, column=0, columnspan=2, sticky="ew", padx=6, pady=(10, 4))
        for col, (text, cmd) in enumerate(
            (
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

        self.lbl_ports = ctk.CTkLabel(
            box_grid, text="", anchor="w", justify="left", font=ctk.CTkFont(size=theme.SIZE_TITLE)
        )
        self.lbl_ports.grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10)
        )

    # -- actions -----------------------------------------------------------

    def _detect(self) -> None:
        try:
            ports = [p["device"] for p in self.ctx.hardware.list_ports()]
        except Exception as exc:  # noqa: BLE001 - display only
            self._msg = f"port list failed: {exc!r}"
            ports = []
        self.menu_ports.configure(values=ports or ["No ports found"])
        self.menu_ports.set(ports[0] if ports else "No ports found")
        self._refresh()

    def _connect(self) -> None:
        port = str(self._readers["serial_port"]()).strip() or None
        try:
            baud = int(str(self._readers["serial_baudrate"]()))
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
