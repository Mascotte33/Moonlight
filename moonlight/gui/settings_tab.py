"""Settings tab: named configs and core options (T2.F2.K1).

The config dropdown picks a named config in ``configs/`` and applies it
live. Every control saves into the picked config as it changes; Save
writes it again on demand, New copies it under a new name and picks the
copy, Delete removes it (T0.F1.K3, T0.F1.K5). Start/Pause/Kill live in the top bar; port,
baud rate and simulation live in Hardware.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from moonlight.core.client_hotkeys import OFFICIAL_CLIENT_FILE, bot_keys, write_moonlight_set
from moonlight.core.config import ConfigError
from moonlight.gui import theme
from moonlight.gui.loop_factory import (
    config_names,
    delete_config,
    load_real_config,
    picked_config,
    save_config_as,
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
    write_option,
)

__all__ = [
    "ALARMS_OPTIONS",
    "EXTRAS_OPTIONS",
    "MODULE_SWITCHES",
    "OPTIONS",
    "ROTATION_OPTIONS",
    "SettingsTab",
    "plain_reason",
]

OPTIONS = [
    Option("Tick rate (Hz)", "loop.tick_rate", "slider", min=1, max=60),
    Option("Game window", "window_title", "dropdown"),
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
#: Rotation has no switch: the accounts typed into the config file turn it on (docs).
ROTATION_OPTIONS = [Option("Logout hotkey", "rotation.logout_hotkey", "hotkey")]

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
    """Named configs (pick, New, Save, Delete) and core options."""

    title = "Settings"
    options = [*OPTIONS, *MODULE_SWITCHES, *EXTRAS_OPTIONS, *ALARMS_OPTIONS, *ROTATION_OPTIONS]

    def build(self, parent: ctk.CTkFrame) -> None:
        io_box = section(parent, "Config")
        io_box.pack(fill="x", padx=10, pady=(10, 4))
        row = Row(io_box)
        row.pack(fill="x", padx=theme.PAD, pady=theme.UNIT)
        self.menu_config = ctk.CTkOptionMenu(
            row,
            values=config_names() or [picked_config().stem],
            command=self._on_pick,
            fg_color=theme.RAISED,
            button_color=theme.RAISED,
            button_hover_color=theme.HOVER,
        )
        self.menu_config.set(picked_config().stem)
        self.menu_config.pack(side="left")
        for text, command, color, hover in (
            ("New", self._on_new, theme.RAISED, theme.HOVER),
            ("Save", self._on_save, theme.RAISED, theme.HOVER),
            ("Delete", self._on_delete, theme.BLOOD, theme.BLOOD_HOVER),
        ):
            ctk.CTkButton(
                row, text=text, width=80, command=command, fg_color=color, hover_color=hover
            ).pack(side="left", padx=(theme.UNIT, 0))
        self.lbl_io = Label(io_box, text="", anchor="w", text_color=theme.TEXT_DIM)
        self.lbl_io.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))

        self.box_options = section(parent, "Options")
        self.box_options.pack(fill="x", padx=10, pady=4)
        self.build_options(self.box_options, [*OPTIONS, *MODULE_SWITCHES])
        box_extras = section(parent, "Extras")
        box_extras.pack(fill="x", padx=10, pady=4)
        self.build_options(box_extras, EXTRAS_OPTIONS)
        box_alarms = section(parent, "Alarms")
        box_alarms.pack(fill="x", padx=10, pady=4)
        self.build_options(box_alarms, ALARMS_OPTIONS)
        box_scripts = section(parent, "Scripts")
        box_scripts.pack(fill="x", padx=10, pady=4)
        self.build_options(box_scripts, ROTATION_OPTIONS)
        self._build_client_hotkeys(parent)
        rows = [w for w in self.box_options.winfo_children() if isinstance(w, Row)]
        Label(
            self.box_options,
            text="MODULES",
            anchor="w",
            font=ctk.CTkFont(size=theme.SIZE_TITLE, weight="bold"),
            text_color=theme.TEXT_MUTED,
        ).pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 0), before=rows[len(OPTIONS)])
        window_row = rows[[o.field for o in OPTIONS].index("window_title")]
        (self.menu_window,) = [
            w for w in window_row.winfo_children() if isinstance(w, ctk.CTkOptionMenu)
        ]
        ctk.CTkButton(
            window_row,
            text="Refresh",
            width=80,
            command=self._refresh_windows,
            fg_color=theme.RAISED,
            hover_color=theme.HOVER,
        ).pack(side="right", padx=theme.UNIT)
        self._refresh_windows()
        self._reasons: dict[str, Label] = {}
        for option, row in zip([*OPTIONS, *MODULE_SWITCHES], rows, strict=True):
            for switch in row.winfo_children():
                if isinstance(switch, ctk.CTkSwitch):
                    switch.configure(command=lambda f=option.field: self._check_switch(f))
                    reason = Label(row, text="", anchor="w", text_color=theme.BLOOD)
                    reason.pack(side="left", padx=theme.UNIT)
                    self._reasons[option.field] = reason

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
        if any(t.startswith(cfg.window_title) for t in window_titles()):
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

    def _refresh_windows(self) -> None:
        self.menu_window.configure(values=window_titles() or ["No windows found"])

    def _check_switch(self, field: str) -> None:
        """Flip a module switch back when the config would refuse it, else apply it."""
        cfg = load_real_config()
        try:
            for option in self.options:
                cfg = write_option(cfg, option, self._readers[option.field]())
        except ConfigError as exc:
            self._writers[field](not self._readers[field]())
            self._reasons[field].configure(text=plain_reason(str(exc)))
            return
        self._reasons[field].configure(text="")
        self.submit()

    # -- named configs -------------------------------------------------------

    def _on_pick(self, name: str) -> None:
        self.ctx.pick_config(name)
        self.lbl_io.configure(text=f"picked {name}")

    def _on_save(self) -> None:
        """Write the picked config to its file."""
        try:
            load_real_config().save(picked_config())
        except (ConfigError, OSError) as exc:
            self.lbl_io.configure(text=str(exc))
            return
        self.lbl_io.configure(text=f"saved {self.menu_config.get()}")

    def _on_new(self) -> None:
        """Copy the picked config under a new name and pick the copy."""
        name = ctk.CTkInputDialog(text="Config name:", title="New config").get_input()
        if not name:
            return
        cfg = load_real_config()
        try:
            try:
                save_config_as(cfg, name)
            except FileExistsError:
                if not messagebox.askyesno("New config", f"{name} exists. Replace it?"):
                    return
                save_config_as(cfg, name, overwrite=True)
        except ConfigError as exc:
            self.lbl_io.configure(text=str(exc))
            return
        self.menu_config.configure(values=config_names())
        self.menu_config.set(name)
        self._on_pick(name)
        self.lbl_io.configure(text=f"saved {name}")

    def _on_delete(self) -> None:
        name = self.menu_config.get()
        if not messagebox.askyesno("Delete config", f"Delete {name}?"):
            return
        try:
            picked = delete_config(name)
        except ConfigError as exc:
            self.lbl_io.configure(text=str(exc))
            return
        self.menu_config.configure(values=config_names())
        self.menu_config.set(picked.stem)
        self._on_pick(picked.stem)
        self.lbl_io.configure(text=f"deleted {name}")
