"""Spell attack tab — AoE vs single-target spell groups + mana reserve (T39).

Edits :class:`~moonlight.gui.config_bridge.SpellAttackParams`. These are part
of the targeting/spell configuration and are consumed by the spell-attack
decision module once it lands (T24); the GUI edits + persists them now.
Validation reuses the params' own ``validate()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui.config_bridge import BotConfig, SpellAttackParams
from moonlight.gui.tabs.base import Tab, section

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState

__all__ = ["SpellAttackTab"]


class SpellAttackTab(Tab):
    """Single-target vs AoE spell groups and the mana reserve."""

    title = "Spell Attack"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        box = section(parent, "Spell groups")
        box.pack(fill="both", expand=True, padx=10, pady=10)
        box_grid = ctk.CTkFrame(box, fg_color="transparent")
        box_grid.pack(fill="both", expand=True)
        box_grid.grid_columnconfigure(1, weight=1)

        self.var_single_on = ctk.BooleanVar()
        self.var_aoe_on = ctk.BooleanVar()
        self.var_single_combo = ctk.StringVar()
        self.var_aoe_combo = ctk.StringVar()
        self.var_mana = ctk.StringVar()

        self.chk_single = ctk.CTkCheckBox(
            box_grid, text="Single-target group enabled", variable=self.var_single_on
        )
        self.chk_single.grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        ctk.CTkLabel(box_grid, text="Single-target combo", anchor="w").grid(
            row=1, column=0, sticky="w", padx=8, pady=4
        )
        self.ent_single = ctk.CTkEntry(box_grid, textvariable=self.var_single_combo, width=160)
        self.ent_single.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        self.chk_aoe = ctk.CTkCheckBox(box_grid, text="AoE group enabled", variable=self.var_aoe_on)
        self.chk_aoe.grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        ctk.CTkLabel(box_grid, text="AoE combo", anchor="w").grid(
            row=3, column=0, sticky="w", padx=8, pady=4
        )
        self.ent_aoe = ctk.CTkEntry(box_grid, textvariable=self.var_aoe_combo, width=160)
        self.ent_aoe.grid(row=3, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(box_grid, text="Mana reserve % (keep MP above this)", anchor="w").grid(
            row=4, column=0, sticky="w", padx=8, pady=4
        )
        self.ent_mana = ctk.CTkEntry(box_grid, textvariable=self.var_mana, width=160)
        self.ent_mana.grid(row=4, column=1, sticky="w", padx=8, pady=4)

        io_box = section(parent, "Apply / live MP")
        io_box.pack(fill="x", padx=10, pady=(4, 10))
        self.btn_apply = ctk.CTkButton(io_box, text="Apply + save", width=130)
        self.btn_apply.pack(side="left", padx=(10, 4), pady=8)
        self.lbl_msg = ctk.CTkLabel(io_box, text="", anchor="w")
        self.lbl_msg.pack(side="left", padx=12, pady=8)
        self.btn_apply.configure(command=self._on_apply)
        self._load(self.ctx.active_config())

    def _load(self, cfg: BotConfig) -> None:
        s: SpellAttackParams = cfg.spells
        self.var_single_on.set(s.single_target_enabled)
        self.var_aoe_on.set(s.aoe_enabled)
        self.var_single_combo.set(s.single_spell_combo)
        self.var_aoe_combo.set(s.aoe_spell_combo)
        self.var_mana.set(str(s.mana_reserve_percent))

    def _collect(self) -> SpellAttackParams:
        try:
            mana = float(self.var_mana.get())
        except ValueError as exc:
            raise ValueError("mana reserve must be a number") from exc
        params = SpellAttackParams(
            single_target_enabled=bool(self.var_single_on.get()),
            aoe_enabled=bool(self.var_aoe_on.get()),
            single_spell_combo=self.var_single_combo.get().strip(),
            aoe_spell_combo=self.var_aoe_combo.get().strip(),
            mana_reserve_percent=mana,
        )
        params.validate()  # raises ValueError on bad values
        return params

    def _on_apply(self) -> None:
        try:
            params = self._collect()
        except ValueError as exc:
            self.lbl_msg.configure(text=f"invalid: {exc}")
            return

        def mutate(cfg: BotConfig) -> None:
            cfg.spells = params

        try:
            self.ctx.config_store.update(mutate)
        except ValueError as exc:
            self.lbl_msg.configure(text=f"invalid: {exc}")
            return
        self.lbl_msg.configure(text="applied + saved")

    def on_poll(self, snap: WorldState) -> None:
        mp = snap.mp_percent
        self.lbl_msg.configure(text=f"live MP {mp:.0f}%")
