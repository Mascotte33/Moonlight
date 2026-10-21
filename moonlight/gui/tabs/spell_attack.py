"""Spell attack tab: attack spells, their hotkeys and the mana reserve (T6.F1.K1).

Same layout as the Healing tab: ``OPTIONS`` plus a hotkey box next to each
spell, saved into ``hotkeys`` by :func:`apply_attack_spells`.
"""

from __future__ import annotations

import customtkinter as ctk

from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config, picked_config
from moonlight.gui.tabs.base import Option, Tab, read_option, section, write_option
from moonlight.setup.config import BotConfig, ConfigError

__all__ = ["OPTIONS", "SPELLS", "SpellAttackTab", "apply_attack_spells"]

OPTIONS = [
    Option("Single-target spell", "attack_spells.single_spell", "entry"),
    Option("AoE spell", "attack_spells.aoe_spell", "entry"),
    Option("AoE from monsters", "attack_spells.aoe_min_monsters", "slider", min=1, max=10),
    Option("Keep mana above %", "attack_spells.mana_reserve_percent", "slider", min=0, max=100),
    Option("Cooldown (ms)", "attack_spells.cooldown_ms", "slider", min=0, max=5000),
]
SPELLS = [o.field for o in OPTIONS if o.field.endswith("_spell")]


# ponytail: same shape as healing.apply_healing; share it if a third tab needs hotkeys.
def apply_attack_spells(
    cfg: BotConfig, values: dict[str, object], combos: dict[str, str]
) -> BotConfig:
    """*cfg* with the option *values* and each spell's hotkey from *combos*.

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
    for option in OPTIONS:
        if option.field in values:
            cfg = write_option(cfg, option, values[option.field])
    return cfg


class SpellAttackTab(Tab):
    """Attack spells, saved into the picked config."""

    title = "Spell Attack"
    options = OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        box = section(parent, "Attack spells")
        box.pack(fill="x", padx=10, pady=10)
        self.build_options(box)
        rows = [w for w in box.winfo_children() if isinstance(w, ctk.CTkFrame)]
        self._hotkeys: dict[str, ctk.CTkEntry] = {}
        for option, row in zip(OPTIONS, rows, strict=True):
            if option.field in SPELLS:
                entry = ctk.CTkEntry(
                    row,
                    width=70,
                    placeholder_text="hotkey",
                    fg_color=theme.NIGHT,
                    border_color=theme.RAISED,
                )
                entry.pack(side="right", padx=theme.UNIT)
                self._hotkeys[option.field] = entry
        self.lbl_msg = ctk.CTkLabel(box, text="", anchor="w", text_color=theme.BLOOD)
        self.lbl_msg.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, theme.PAD))
        self._fill_hotkeys(load_real_config())

    def _fill_hotkeys(self, cfg: BotConfig) -> None:
        combos = cfg.spell_combos
        for field, entry in self._hotkeys.items():
            entry.delete(0, "end")
            combo = combos.get(str(read_option(cfg, field)), "")
            if combo:
                entry.insert(0, combo)

    def reload_options(self) -> None:
        super().reload_options()
        self._fill_hotkeys(load_real_config())

    def apply(self) -> BotConfig:
        path = picked_config()
        values = {field: read() for field, read in self._readers.items()}
        combos = {field: entry.get() for field, entry in self._hotkeys.items()}
        try:
            cfg = apply_attack_spells(load_real_config(path), values, combos)
        except ConfigError as exc:
            self.lbl_msg.configure(text=str(exc))
            raise
        self.lbl_msg.configure(text="")
        cfg.save(path)
        return cfg
