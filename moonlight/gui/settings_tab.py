"""Settings tab: this PC's settings, shared by every profile (T2.F2.K1).

Tick rate, walking keys and the client's hotkeys (``data/machine.json``).
The profile picker and module switches live on the Dashboard; extras and
alarms have their own tabs (docs/prs/profiles.md#layout). Port, baud rate
and simulation live in Hardware. The option lists of the other tabs stay
defined here.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from moonlight.core.client_hotkeys import OFFICIAL_CLIENT_FILE, bot_keys, write_moonlight_set
from moonlight.gui import theme
from moonlight.gui.loop_factory import (
    load_real_config,
    picked_config,
    window_titles,
)
from moonlight.gui.tab_base import (
    Label,
    Option,
    Row,
    Tab,
    row_button,
    row_entry,
    section,
)
from moonlight.x11 import is_tibia_title

__all__ = [
    "ALARMS_OPTIONS",
    "EXTRAS_OPTIONS",
    "MODULE_SWITCHES",
    "OPTIONS",
    "PROFILE_OPTIONS",
    "ROTATION_OPTIONS",
    "SettingsTab",
    "plain_reason",
]

# doc: docs/prs/profiles.md#level-range
#: The picked profile's level range (0 and 0: never picked by level).
PROFILE_OPTIONS = [
    Option("Min level", "min_level", "entry"),
    Option("Max level", "max_level", "entry"),
]
OPTIONS = [
    Option("Tick rate (Hz)", "loop.tick_rate", "slider", min=1, max=60),
    Option("Cavebot walks with WSAD keys", "walk_keys", "switch"),
]
#: The only switch for each module (T2.F4.K2); cavebot has its own on its tab.
MODULE_SWITCHES = [
    Option(name.replace("_", " ").capitalize(), f"{name}.enabled", "switch")
    for name in ("healing", "targeting", "looting")
]
#: One row per extra: its hotkey and checkbox, then the player limits (docs/prs/extras.md#extras).
EXTRAS_OPTIONS = [
    option
    for name, label in (
        ("food", "Eat food when hungry"),
        ("haste", "Haste while walking"),
        ("buff", "Buff potion"),
        ("spell_buff", "Spell buff"),
        ("ring", "Ring when the slot is empty"),
        ("amulet", "Amulet when the slot is empty"),
    )
    # The hotkey first: an extra turned on without one is refused.
    for option in (
        Option(label, f"extras.{name}_hotkey", "hotkey"),
        Option("", f"extras.{name}", "checkbox"),
    )
]
EXTRAS_OPTIONS.insert(
    EXTRAS_OPTIONS.index(Option("", "extras.haste", "checkbox")) + 1,
    Option("Haste min mana (points)", "extras.haste_min_mana", "entry"),
)
#: HP, player and PM alarms: alert and screenshot, the bot goes on (docs/prs/extras.md#alarms).
ALARMS_OPTIONS = [
    Option("Low HP below (%)", "alarms.low_hp_threshold", "entry"),
    Option("", "alarms.low_hp_alarm", "checkbox"),
    Option("Fast HP drop in 1 s (HP %)", "alarms.fast_hp_drop_threshold", "entry"),
    Option("", "alarms.fast_hp_drop_alarm", "checkbox"),
    Option("Player in sight longer than (min, 0 = at once)", "extras.player_minutes", "entry"),
    Option("", "extras.player_alarm", "checkbox"),
    Option("Staying player: go refill", "extras.player_refill", "checkbox"),
    Option("Private message", "extras.pm_alarm", "checkbox"),
]
#: Rotation has no switch: picking an accounts file turns it on (docs/prs/stamina-switch.md).
ROTATION_OPTIONS = [
    Option("Account rotation on", "rotation.enabled", "switch"),
    Option("Cavebot rotation on", "cavebot_rotate", "switch"),
    Option("Logout hotkey", "rotation.logout_hotkey", "hotkey"),
    Option("Accounts file", "rotation.accounts_file", "entry"),
    Option("Tibia program", "rotation.client_exe", "entry"),
]

#: Bot refusal text -> what the user reads next to the switch (T2.F4.K3).
REASONS = [
    ("cavebot.route", "Cavebot needs a route. Pick one in the Route dropdown or record one below."),
]


def plain_reason(message: str) -> str:
    """The plain-words reason for a refused switch, or *message* itself."""
    for key, text in REASONS:
        if key in message:
            return text
    return message


class SettingsTab(Tab):
    """This PC's settings: tick rate, walking keys and the client's hotkeys."""

    title = "Settings"
    options = OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        box = section(parent, "This PC")
        box.pack(fill="x", padx=10, pady=(10, 4))
        Label(
            box,
            text="Shared by every profile. Hunt settings: Dashboard and each module's tab.",
            anchor="w",
            text_color=theme.TEXT_DIM,
        ).pack(fill="x", padx=theme.PAD)
        self.build_options(box)
        self._build_client_hotkeys(parent)

    # -- client hotkeys ------------------------------------------------------
    # doc: docs/prs/client-hotkeys.md#moonlight-set

    def _build_client_hotkeys(self, parent: ctk.CTkFrame) -> None:
        box = section(parent, "Client hotkeys")
        box.pack(fill="x", padx=10, pady=4)
        file_row = Row(box)
        file_row.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        Label(file_row, text="Client file", anchor="w", text_color=theme.TEXT_DIM).pack(side="left")
        self._client_file = row_entry(file_row, "", 420, self._save_client_hotkeys)
        row_button(file_row, "Browse", self._browse_client_file, width=80)
        self._client_keys_box = ctk.CTkFrame(box, fg_color="transparent")
        self._client_keys_box.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        self._client_texts: dict[str, ctk.CTkEntry] = {}
        self._client_combos: list[str] | None = None
        button_row = Row(box)
        button_row.pack(fill="x", padx=theme.PAD, pady=theme.UNIT)
        row_button(button_row, "Write Moonlight hotkeys", self._write_client_hotkeys, width=180)
        self.lbl_client = Label(box, text="", anchor="w", text_color=theme.TEXT_DIM, wraplength=600)
        self.lbl_client.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))
        self.ctx.config_listeners.append(self._fill_client_hotkeys)
        self._fill_client_hotkeys()

    def _fill_client_hotkeys(self) -> None:
        """Refill the client file and one row per bot key (rebuilt when the keys change)."""
        cfg = load_real_config()
        self._client_file.delete(0, "end")
        self._client_file.insert(0, str(cfg.client_options_file or OFFICIAL_CLIENT_FILE))
        keys = bot_keys(cfg)
        if [k.combo for k in keys] == self._client_combos:
            return
        self._client_combos = [k.combo for k in keys]
        for child in self._client_keys_box.winfo_children():
            child.destroy()
        self._client_texts = {}
        for key in keys:
            row = Row(self._client_keys_box)
            row.pack(fill="x", pady=(theme.UNIT, 0))
            ctk.CTkLabel(row, text=key.combo, width=90, anchor="w").pack(side="left")
            ctk.CTkLabel(
                row, text=key.purpose, width=200, anchor="w", text_color=theme.TEXT_DIM
            ).pack(side="left")
            if key.client_action:
                Label(row, text="client action", anchor="w", text_color=theme.TEXT_MUTED).pack(
                    side="left", padx=(theme.UNIT, 0)
                )
                continue
            text = cfg.client_hotkeys.get(key.combo, key.suggested)
            self._client_texts[key.combo] = row_entry(row, text, 260, self._save_client_hotkeys)

    def _browse_client_file(self) -> None:
        name = filedialog.askopenfilename(
            title="The client's conf/clientoptions.json",
            filetypes=[("Client options", "clientoptions.json"), ("JSON", "*.json")],
        )
        if name:
            self._client_file.delete(0, "end")
            self._client_file.insert(0, name)
            self._save_client_hotkeys()

    def _save_client_hotkeys(self) -> None:
        """Save the client file and typed texts; the running bot doesn't use them."""
        cfg = load_real_config()
        texts = dict(cfg.client_hotkeys)
        texts.update({combo: entry.get().strip() for combo, entry in self._client_texts.items()})
        typed = Path(self._client_file.get().strip())
        client_file = None if typed in (Path(), OFFICIAL_CLIENT_FILE) else typed
        replace(cfg, client_options_file=client_file, client_hotkeys=texts).save(picked_config())

    def _write_client_hotkeys(self) -> None:
        self._save_client_hotkeys()
        cfg = load_real_config()
        # ponytail: matches the game window title, not the client's folder.
        if any(is_tibia_title(t) for t in window_titles()):
            self.lbl_client.configure(
                text="Close the game client first: it writes its own hotkeys back when it closes."
            )
            return
        path = cfg.client_options_file or OFFICIAL_CLIENT_FILE
        try:
            report = write_moonlight_set(path, bot_keys(cfg), cfg.client_hotkeys)
        except (OSError, ValueError, KeyError) as exc:
            self.lbl_client.configure(text=f"Not written: {exc}")
            return
        for line in report:
            self.ctx.log.info(f"client hotkeys: {line}")
        self.lbl_client.configure(
            text=f"Wrote the Moonlight set to {path} (the old file is {path.name}.bak). "
            "Open the client: the set is in use."
        )
