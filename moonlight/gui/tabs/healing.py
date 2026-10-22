"""Healing tab: HP/MP thresholds, heal spells, potions and their hotkeys (T4.F1.K1).

The thresholds and spells are ``OPTIONS``. Each spell also gets a hotkey
box that writes ``hotkeys[<spell>]``; ``hotkeys`` is a dict, so that part
lives in :func:`apply_healing`, not in ``OPTIONS``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config, picked_config
from moonlight.gui.tabs.base import Option, Tab, read_option, section, write_option
from moonlight.setup.config import BotConfig, ConfigError

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState

__all__ = ["GROUPS", "OPTIONS", "SPELLS", "HealingTab", "apply_healing"]

#: Each heal's HP % with its spell just under it (T4.F1.K2).
GROUPS = [
    ("Emergency", [
        Option("Heal at HP %", "healing.emergency_hp_percent", "slider", min=0, max=100),
        Option("Spell", "healing.emergency_spell", "entry"),
    ]),
    ("Normal", [
        Option("Heal at HP %", "healing.normal_hp_percent", "slider", min=0, max=100),
        Option("Spell", "healing.normal_spell", "entry"),
    ]),
    # T4.F1.K3: each condition has its own cure; a blank spell is not cured.
    ("Cure", [
        Option("Paralyze (utani hur)", "healing.cure_paralyze", "entry"),
        Option("Poison (exana pox)", "healing.cure_poison", "entry"),
        Option("Burning (exana flam)", "healing.cure_burn", "entry"),
        Option("Electrified (exana vis)", "healing.cure_energy", "entry"),
        Option("Cursed (exana mort)", "healing.cure_curse", "entry"),
        Option("Bleeding (exana kor)", "healing.cure_bleed", "entry"),
    ]),
    # T4.F2.K1: a potion is used at its % even when mana is too low for spells.
    ("Potions", [
        Option("HP potion at HP %", "healing.hp_potion_percent", "slider", min=0, max=100),
        Option("HP potion", "healing.hp_potion", "entry"),
        Option("MP potion at MP %", "healing.mp_potion_percent", "slider", min=0, max=100),
        Option("MP potion", "healing.mp_potion", "entry"),
    ]),
    ("Mana and cooldown", [
        Option("Keep mana above %", "healing.mana_threshold", "slider", min=0, max=100),
        Option("Heal cooldown (ms)", "healing.heal_cooldown_ms", "slider", min=0, max=2000),
    ]),
]
OPTIONS = [o for _, group in GROUPS for o in group]
SPELLS = [o.field for o in OPTIONS if o.control == "entry"]


def apply_healing(
    cfg: BotConfig, values: dict[str, object], combos: dict[str, str]
) -> BotConfig:
    """*cfg* with the option *values* and each spell's hotkey from *combos*.

    *values* maps option fields to control values; *combos* maps a spell
    field to its hotkey. A blank hotkey adds nothing, so a spell without
    one is refused by :meth:`BotConfig.validate`.

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


class HealingTab(Tab):
    """Healing thresholds and spells, saved into the picked config."""

    title = "Healing"
    options = OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        self._hotkeys: dict[str, ctk.CTkEntry] = {}
        for title, group in GROUPS:
            box = section(parent, title)
            box.pack(fill="x", padx=10, pady=(10, 0))
            self.build_options(box, group)
            rows = [w for w in box.winfo_children() if isinstance(w, ctk.CTkFrame)]
            for option, row in zip(group, rows, strict=True):
                if option.field not in SPELLS:
                    continue
                entry = ctk.CTkEntry(
                    row,
                    width=70,
                    placeholder_text="hotkey",
                    fg_color=theme.NIGHT,
                    border_color=theme.RAISED,
                )
                entry.pack(side="right", padx=theme.UNIT)
                self._hotkeys[option.field] = entry
            ctk.CTkFrame(box, height=theme.PAD, fg_color="transparent").pack()
        self.lbl_msg = ctk.CTkLabel(
            parent, text="", anchor="w", text_color=theme.BLOOD
        )
        self.lbl_msg.pack(fill="x", padx=10, pady=(theme.UNIT, 0))
        self.lbl_live = ctk.CTkLabel(
            parent, text="", anchor="w", font=ctk.CTkFont(family=theme.MONO)
        )
        self.lbl_live.pack(fill="x", padx=10)
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
            cfg = apply_healing(load_real_config(path), values, combos)
        except ConfigError as exc:
            self.lbl_msg.configure(text=str(exc))
            raise
        self.lbl_msg.configure(text="")
        cfg.save(path)
        return cfg

    def on_poll(self, snap: WorldState) -> None:
        text = f"live HP {snap.hp_percent:.0f}%  MP {snap.mp_percent:.0f}%"
        if self.lbl_live.cget("text") != text:
            self.lbl_live.configure(text=text)
