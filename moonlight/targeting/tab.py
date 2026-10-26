"""Targeting tab: the attack hotkey and the attack spells.

Monster rows are edited in the config file until the rows table lands.
Each attack spell gets a hotkey box, saved into ``hotkeys`` by
:func:`apply_attack_spells` (T5.F4.K1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.core.config import BotConfig, ConfigError
from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config, picked_config
from moonlight.gui.tab_base import HotkeyBox, Option, Tab, read_option, section, write_option

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.core.state import WorldState

__all__ = [
    "ATTACK_OPTIONS",
    "OPTIONS",
    "SPELLS",
    "TargetingTab",
    "apply_attack_spells",
    "apply_targeting",
]

OPTIONS = [
    Option("Attack hotkey", "loop.attack_combo", "hotkey"),
]
ATTACK_OPTIONS = [
    Option("Single-target spell", "attack_spells.single_spell", "entry"),
    Option("AoE spell", "attack_spells.aoe_spell", "entry"),
    Option("AoE from monsters", "attack_spells.aoe_min_monsters", "slider", min=1, max=10),
    Option("Keep mana above %", "attack_spells.mana_reserve_percent", "slider", min=0, max=100),
    Option("Cooldown (ms)", "attack_spells.cooldown_ms", "slider", min=0, max=5000),
]
SPELLS = [o.field for o in ATTACK_OPTIONS if o.field.endswith("_spell")]


def apply_targeting(cfg: BotConfig, values: dict[str, object]) -> BotConfig:
    """*cfg* with the option *values*.

    Raises:
        ConfigError: For a refused value.
    """
    for option in OPTIONS:
        if option.field in values:
            cfg = write_option(cfg, option, values[option.field])
    return cfg


# ponytail: same shape as healing.apply_healing; share it if a third tab needs hotkeys.
def apply_attack_spells(
    cfg: BotConfig, values: dict[str, object], combos: dict[str, str]
) -> BotConfig:
    """*cfg* with the attack spell *values* and each spell's hotkey from *combos*.

    Raises:
        ConfigError: For a refused value or a spell with no hotkey.
    """
    data = cfg.to_dict()
    hotkeys = dict(data.get("hotkeys") or {})
    for field, combo in combos.items():
        spell = str(values[field]).strip()
        if spell and combo.strip():
            hotkeys[spell] = combo.strip()
    data["hotkeys"] = hotkeys
    cfg = BotConfig.from_dict(data)
    for option in ATTACK_OPTIONS:
        if option.field in values:
            cfg = write_option(cfg, option, values[option.field])
    return cfg


class TargetingTab(Tab):
    """Targeting rules and attack spells, saved into the picked config."""

    title = "Targeting"
    options = OPTIONS + ATTACK_OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        box = section(parent, "Targeting")
        box.pack(fill="x", padx=10, pady=(10, 0))
        self.build_options(box, OPTIONS)
        spells = section(parent, "Attack spells")
        spells.pack(fill="x", padx=10, pady=10)
        self.build_options(spells, ATTACK_OPTIONS)
        rows = [w for w in spells.winfo_children() if isinstance(w, ctk.CTkFrame)]
        self._hotkeys: dict[str, HotkeyBox] = {}
        for option, row in zip(ATTACK_OPTIONS, rows, strict=True):
            if option.field in SPELLS:
                hotkey = HotkeyBox(row, self.submit)
                hotkey.pack(side="right", padx=theme.UNIT)
                self._hotkeys[option.field] = hotkey
        self.lbl_msg = ctk.CTkLabel(spells, text="", anchor="w", text_color=theme.BLOOD)
        self.lbl_msg.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, theme.PAD))
        cfg = load_real_config()
        self._fill_hotkeys(cfg)
        self.lbl_live = ctk.CTkLabel(parent, text="", anchor="w", text_color=theme.TEXT_DIM)
        self.lbl_live.pack(fill="x", padx=10)

    def _fill_hotkeys(self, cfg: BotConfig) -> None:
        combos = cfg.spell_combos
        for field, hotkey in self._hotkeys.items():
            hotkey.set(combos.get(str(read_option(cfg, field)), ""))

    def reload_options(self) -> None:
        super().reload_options()
        self._fill_hotkeys(load_real_config())

    def apply(self) -> BotConfig:
        path = picked_config()
        values = {field: read() for field, read in self._readers.items()}
        combos = {field: hotkey.get() for field, hotkey in self._hotkeys.items()}
        try:
            cfg = apply_targeting(load_real_config(path), values)
            cfg = apply_attack_spells(cfg, values, combos)
        except ConfigError as exc:
            self.lbl_msg.configure(text=str(exc))
            raise
        self.lbl_msg.configure(text="")
        cfg.save(path)
        return cfg

    def on_poll(self, snap: WorldState) -> None:
        names = ", ".join(m.name for m in snap.monsters) or "none"
        text = f"creatures on screen: {names}"
        if self.lbl_live.cget("text") != text:
            self.lbl_live.configure(text=text)
