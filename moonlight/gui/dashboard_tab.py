"""Dashboard tab: stat cards, bot health, battle list and log tail (T1).

Five stat cards (Uptime, Ticks, Creatures, HP, Stamina) show hero-size values from
the loop's stats and the live :class:`WorldState`. The module switches live
in Settings. A Machine line shows the HWID and the public IP.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import urllib.request

import customtkinter as ctk

from moonlight.core.state import WorldState
from moonlight.gui import theme
from moonlight.gui.state_bridge import LogFeed
from moonlight.gui.tab_base import Label, Row, Tab, dot_color, health, section, set_label, stat_num

__all__ = [
    "CARDS",
    "DashboardTab",
    "card_values",
    "hardware_id",
    "public_ip",
    "recent_errors",
    "uptime_text",
]

log = logging.getLogger(__name__)

CARDS = ("Uptime", "Ticks", "Creatures", "HP", "Stamina")
#: Seconds between public IP lookups (a VPN or router restart changes it).
PUBLIC_IP_REFRESH_SECONDS = 300


# doc: docs/prs/near-targeting.md#machine-line
def hardware_id() -> str:
    """Windows' MachineGuid (stays the same until Windows is reinstalled)."""
    if sys.platform != "win32":
        raise OSError("the HWID is read from the Windows registry")
    import winreg

    key = winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Cryptography",
        0,
        winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
    )
    with key:
        return str(winreg.QueryValueEx(key, "MachineGuid")[0])


def public_ip() -> str:
    """This PC's public IP address, asked from api.ipify.org."""
    with urllib.request.urlopen("https://api.ipify.org", timeout=10) as reply:
        return str(reply.read().decode()).strip()


def uptime_text(stats: dict[str, object]) -> str:
    """``h:mm:ss`` from the loop's ``uptime_seconds``; ``—`` when stopped."""
    if "uptime_seconds" not in stats:
        return "—"
    secs = int(stat_num(stats, "uptime_seconds"))
    return f"{secs // 3600}:{secs // 60 % 60:02d}:{secs % 60:02d}"


def card_values(stats: dict[str, object], snap: WorldState) -> dict[str, str]:
    """The text of each stat card."""
    stamina = stats.get("stamina_minutes")  # doc: docs/prs/client-hotkeys.md#stamina
    return {
        "Uptime": uptime_text(stats),
        "Ticks": str(int(stat_num(stats, "tick_count"))) if stats else "—",
        "Creatures": str(len(snap.monsters)),
        "HP": f"{snap.hp_percent:.0f}%",
        "Stamina": f"{stamina // 60}:{stamina % 60:02d}" if isinstance(stamina, int) else "—",
    }


# doc: docs/prs/gui-log.md#one-log-each
def recent_errors(log: LogFeed) -> str:
    """The last 20 warning and error lines, newest first, or "No errors"."""
    lines = log.text(20, levels=("warn", "error")).splitlines()
    return "\n".join(reversed(lines)) or "No errors"


class DashboardTab(Tab):
    """Stat cards, battle list and event log."""

    title = "Dashboard"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        cards = Row(parent)
        cards.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        self.lbl_task = Label(
            cards,
            text="Current task: Idle",
            anchor="w",
            font=ctk.CTkFont(size=theme.SIZE_HERO, weight="bold"),
            text_color=theme.TEXT,
        )
        self.lbl_task.grid(row=0, column=0, columnspan=len(CARDS), sticky="ew", pady=(0, theme.PAD))
        self._cards: dict[str, Label] = {}
        for col, name in enumerate(CARDS):
            cards.grid_columnconfigure(col, weight=1, uniform="card")
            box = section(cards, name)
            box.grid(row=1, column=col, sticky="ew", padx=(0 if col == 0 else theme.UNIT, 0))
            value = Label(
                box,
                text="—",
                anchor="w",
                font=ctk.CTkFont(family=theme.MONO, size=theme.SIZE_HERO, weight="bold"),
                text_color=theme.TEXT,
            )
            value.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))
            self._cards[name] = value

        box = section(cards, "Bot health")
        box.grid(row=2, column=0, columnspan=len(CARDS), sticky="ew", pady=(theme.UNIT, 0))
        dots = Row(box)
        dots.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))
        self._dots: dict[str, Label] = {}
        for name, _ in health(WorldState(), False, False):
            self._dots[name] = Label(
                dots, text=f"● {name}", text_color=theme.TEXT_MUTED
            )
            self._dots[name].pack(side="left", padx=(0, theme.PAD))

        # doc: docs/prs/near-targeting.md#machine-line
        box = section(cards, "Machine")
        box.grid(row=3, column=0, columnspan=len(CARDS), sticky="ew", pady=(theme.UNIT, 0))
        try:
            self._hardware_id = hardware_id()
        except OSError as exc:
            log.warning("dashboard: cannot read the HWID: %s", exc)
            self._hardware_id = "unknown"
        self._public_ip = "looking up..."
        self.lbl_machine = Label(
            box, text="", anchor="w", font=ctk.CTkFont(family=theme.MONO), text_color=theme.TEXT
        )
        self.lbl_machine.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))
        threading.Thread(target=self._keep_public_ip, daemon=True, name="public-ip").start()

        body = Row(parent)
        body.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=3)
        body.grid_rowconfigure(2, weight=2)

        self.battle_box = section(body, "Battle list")
        self.battle_box.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        self.battle_list = ctk.CTkTextbox(self.battle_box, height=120, wrap="word")
        self.battle_list.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.battle_list.configure(state="disabled")

        self.log_box = section(body, "Warnings and errors (all lines: Diagnostics)")
        self.log_box.grid(row=2, column=0, sticky="nsew")
        self.log_view = ctk.CTkTextbox(self.log_box, height=100, wrap="none")
        self.log_view.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.log_view.configure(state="disabled")
        self._last_log: str = ""
        self._last_battle: str = ""

    def _keep_public_ip(self) -> None:
        """Look the public IP up now and every few minutes (thread: never blocks the GUI)."""
        while True:
            try:
                self._public_ip = public_ip()
            except OSError as exc:
                log.warning("dashboard: public IP lookup failed: %s", exc)
                self._public_ip = "unknown (no internet?)"
            time.sleep(PUBLIC_IP_REFRESH_SECONDS)

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
        machine = f"HWID {self._hardware_id}    Public IP {self._public_ip}"
        if self.lbl_machine.cget("text") != machine:
            self.lbl_machine.configure(text=machine)
        arduino = self.ctx.hardware.backend is not None
        for name, ok in health(snap, arduino, self.ctx.bot_running()):
            set_label(self._dots[name], color=dot_color(ok))
        battle = "empty"
        if snap.monsters:
            monsters = snap.monsters[:20]
            lines = [
                f"{i + 1}. {m.name}  ({m.hp_percent:.0f}% HP)" for i, m in enumerate(monsters)
            ]
            if snap.selected_target is not None:
                lines.insert(0, f"> selected: {snap.selected_target.name}")
            battle = "\n".join(lines)
        if battle != self._last_battle:
            self._set_text(self.battle_list, battle)
            self._last_battle = battle
        log_text = recent_errors(self.ctx.log)
        if log_text != self._last_log:
            self._set_text(self.log_view, log_text)
            self._last_log = log_text
