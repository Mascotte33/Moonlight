"""Targeting tab: engagement rules, the attack hotkey and the creature list.

The creature list is a tuple, so it is saved by :func:`apply_targeting`,
not through ``OPTIONS``.
"""

from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config, picked_config
from moonlight.gui.tabs.base import Option, Tab, read_option, section, write_option
from moonlight.setup.config import BotConfig

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState

__all__ = ["OPTIONS", "TargetingTab", "apply_targeting", "remove_creatures"]

OPTIONS = [
    Option("Max monsters to engage", "targeting.max_monsters_to_engage", "slider", min=1, max=10),
    Option("Prefer lowest-HP monster", "targeting.prefer_low_hp", "switch"),
    Option("Flee when a player is on screen", "targeting.player_protection", "switch"),
    Option("Attack hotkey", "loop.attack_combo", "entry"),
    Option("Creatures", "targeting.mode", "dropdown", choices=["all", "whitelist", "blacklist"]),
]


def apply_targeting(cfg: BotConfig, values: dict[str, object], creatures: list[str]) -> BotConfig:
    """*cfg* with the option *values* and the creature list *creatures*.

    Raises:
        ConfigError: For a refused value.
    """
    data = cfg.to_dict()
    data["modules"]["targeting"]["creatures"] = creatures
    cfg = BotConfig.from_dict(data)
    for option in OPTIONS:
        if option.field in values:
            cfg = write_option(cfg, option, values[option.field])
    return cfg


def remove_creatures(creatures: list[str], picked: tuple[int, ...], typed: str) -> list[str]:
    """*creatures* without the *picked* rows, or without *typed* if none is picked."""
    if picked:
        return [c for i, c in enumerate(creatures) if i not in picked]
    return [c for c in creatures if c.lower() != typed.strip().lower()]


class TargetingTab(Tab):
    """Targeting rules, saved into the picked config."""

    title = "Targeting"
    options = OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        box = section(parent, "Targeting")
        box.pack(fill="x", padx=10, pady=10)
        self.build_options(box)
        edit = ctk.CTkFrame(box, fg_color="transparent")
        edit.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        self.ent_name = ctk.CTkEntry(
            edit, placeholder_text="creature name", fg_color=theme.NIGHT, border_color=theme.RAISED
        )
        self.ent_name.pack(side="left", fill="x", expand=True)
        for text, command in (("Remove", self._remove), ("Add", self._add)):
            ctk.CTkButton(
                edit,
                text=text,
                width=70,
                command=command,
                fg_color=theme.RAISED,
                hover_color=theme.HOVER,
            ).pack(side="right", padx=(theme.UNIT, 0))
        # Plain Tk Listbox: CTk has no list widget, and this one gives Ctrl/Shift select.
        self.list_creatures = tk.Listbox(
            box,
            selectmode="extended",
            height=6,
            activestyle="none",
            bg=theme.NIGHT,
            fg=theme.TEXT,
            selectbackground=theme.HOVER,
            selectforeground=theme.TEXT,
            highlightthickness=0,
            borderwidth=0,
            font=(theme.MONO, theme.SIZE_BODY),
        )
        self.list_creatures.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, theme.PAD))
        self._fill_creatures(load_real_config())
        self.lbl_live = ctk.CTkLabel(parent, text="", anchor="w", text_color=theme.TEXT_DIM)
        self.lbl_live.pack(fill="x", padx=10)

    def _fill_creatures(self, cfg: BotConfig) -> None:
        names = read_option(cfg, "targeting.creatures")
        self._creatures = list(names) if isinstance(names, tuple) else []
        self._show_creatures()

    def _show_creatures(self) -> None:
        self.list_creatures.delete(0, "end")
        for name in self._creatures:
            self.list_creatures.insert("end", name)

    def _add(self) -> None:
        name = self.ent_name.get().strip()
        if name and name.lower() not in (c.lower() for c in self._creatures):
            self._creatures.append(name)
        self.ent_name.delete(0, "end")
        self._show_creatures()

    def _remove(self) -> None:
        self._creatures = remove_creatures(
            self._creatures, self.list_creatures.curselection(), self.ent_name.get()  # type: ignore[no-untyped-call]
        )
        self.ent_name.delete(0, "end")
        self._show_creatures()

    def reload_options(self) -> None:
        super().reload_options()
        self._fill_creatures(load_real_config())

    def apply(self) -> BotConfig:
        path = picked_config()
        values = {field: read() for field, read in self._readers.items()}
        cfg = apply_targeting(load_real_config(path), values, self._creatures)
        cfg.save(path)
        return cfg

    def on_poll(self, snap: WorldState) -> None:
        names = ", ".join(m.name for m in snap.monsters) or "none"
        text = f"creatures on screen: {names}"
        if self.lbl_live.cget("text") != text:
            self.lbl_live.configure(text=text)
