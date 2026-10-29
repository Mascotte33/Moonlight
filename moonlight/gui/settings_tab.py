"""Settings tab: named configs and core options (T2.F2.K1).

The config dropdown picks a named config in ``configs/`` and applies it
live. Every control saves into the picked config as it changes; Save
writes it again on demand, New copies it under a new name and picks the
copy, Delete removes it (T0.F1.K3, T0.F1.K5). Start/Pause/Kill live in the top bar; port,
baud rate and simulation live in Hardware.
"""

from __future__ import annotations

from tkinter import messagebox

import customtkinter as ctk

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
from moonlight.gui.tab_base import Option, Tab, section, write_option

__all__ = [
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
    Option("Cavebot walks with arrow keys", "walk_keys", "switch"),
]
#: The only switch for each module (T2.F4.K2); cavebot and targeting have theirs on their tabs.
MODULE_SWITCHES = [
    Option(name.replace("_", " ").capitalize(), f"{name}.enabled", "switch")
    for name in (
        "healing", "looting", "scripts", "rotation", "buffs", "alarms"
    )
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
] + [
    Option("Player staying after (min)", "extras.player_minutes", "entry"),
    Option("Staying player: go refill", "extras.player_refill", "checkbox"),
    Option("Staying player: alarm", "extras.player_alarm", "checkbox"),
    Option("Private message: alarm", "extras.pm_alarm", "checkbox"),
]
#: The accounts and passwords are typed into the config file, not here (docs/prs/rotation.md).
ROTATION_OPTIONS = [Option("Logout hotkey", "rotation.logout_hotkey", "hotkey")]

#: Bot refusal text -> what the user reads next to the switch (T2.F4.K3).
REASONS = [
    ("cavebot.route", "Cavebot needs a route. Pick one in the Route dropdown or record one below."),
    ("buff-icon", "Buffs needs a buff-icon reader, which the bot doesn't have yet."),
    ("rotation needs the cavebot", "Rotation needs Cavebot on."),
    ("rotation needs at least", "Rotation needs 2 or more accounts in the config file."),
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
    options = [*OPTIONS, *MODULE_SWITCHES, *EXTRAS_OPTIONS, *ROTATION_OPTIONS]

    def build(self, parent: ctk.CTkFrame) -> None:
        io_box = section(parent, "Config")
        io_box.pack(fill="x", padx=10, pady=(10, 4))
        row = ctk.CTkFrame(io_box, fg_color="transparent")
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
        self.lbl_io = ctk.CTkLabel(io_box, text="", anchor="w", text_color=theme.TEXT_DIM)
        self.lbl_io.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))

        self.box_options = section(parent, "Options")
        self.box_options.pack(fill="x", padx=10, pady=4)
        self.build_options(self.box_options, [*OPTIONS, *MODULE_SWITCHES])
        box_extras = section(parent, "Extras")
        box_extras.pack(fill="x", padx=10, pady=4)
        self.build_options(box_extras, EXTRAS_OPTIONS)
        box_rotation = section(parent, "Account rotation")
        box_rotation.pack(fill="x", padx=10, pady=4)
        self.build_options(box_rotation, ROTATION_OPTIONS)
        rows = [w for w in self.box_options.winfo_children() if isinstance(w, ctk.CTkFrame)]
        ctk.CTkLabel(
            self.box_options,
            text="MODULES",
            anchor="w",
            font=ctk.CTkFont(size=theme.SIZE_TITLE, weight="bold"),
            text_color=theme.TEXT_MUTED,
        ).pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 0), before=rows[len(OPTIONS)])
        ctk.CTkLabel(
            self.box_options,
            text="Each switch turns one bot module on or off. An off module keeps its values.",
            anchor="w",
            text_color=theme.TEXT_DIM,
        ).pack(fill="x", padx=theme.PAD, before=rows[len(OPTIONS)])
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
        self._reasons: dict[str, ctk.CTkLabel] = {}
        for option, row in zip([*OPTIONS, *MODULE_SWITCHES], rows, strict=True):
            for switch in row.winfo_children():
                if isinstance(switch, ctk.CTkSwitch):
                    switch.configure(command=lambda f=option.field: self._check_switch(f))
                    reason = ctk.CTkLabel(row, text="", anchor="w", text_color=theme.BLOOD)
                    reason.pack(side="left", padx=theme.UNIT)
                    self._reasons[option.field] = reason

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
