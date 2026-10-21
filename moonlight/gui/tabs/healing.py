"""Healing tab — HP/MP thresholds for potions/spells (T39).

Edits :class:`~moonlight.gui.config_bridge.HealingParams`, which mirrors the
real ``moonlight.decision.healing.HealingConfig``. Apply writes the values
into the live :class:`~moonlight.gui.config_bridge.ConfigStore` (thread-safe)
and persists them; the bot's loop consumes the mapped ``HealingConfig`` on
its next tick. Validation reuses the real dataclass's ``validate()`` so the
GUI can never store a config the bot would reject.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui.config_bridge import BotConfig, HealingParams
from moonlight.gui.tabs.base import Tab, section

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState

__all__ = ["HealingTab"]


class HealingTab(Tab):
    """Healing thresholds/spells, wired to ``decision.healing.HealingConfig``."""

    title = "Healing"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        box = section(parent, "Healing thresholds")
        box.pack(fill="both", expand=True, padx=10, pady=10)
        box_grid = ctk.CTkFrame(box, fg_color="transparent")
        box_grid.pack(fill="both", expand=True)
        box_grid.grid_columnconfigure(1, weight=1)

        self.var_emg = ctk.StringVar()
        self.var_norm = ctk.StringVar()
        self.var_mana = ctk.StringVar()
        self.var_cooldown = ctk.StringVar()
        self.var_emg_spell = ctk.StringVar()
        self.var_norm_spell = ctk.StringVar()
        self.var_cure_spell = ctk.StringVar()

        self._field(box_grid, 0, "Emergency HP % (<=)", self.var_emg, "120")
        self._field(box_grid, 1, "Normal HP % (<)", self.var_norm, "120")
        self._field(box_grid, 2, "Mana threshold %", self.var_mana, "120")
        self._field(box_grid, 3, "Heal cooldown (ms)", self.var_cooldown, "120")
        self._field(box_grid, 4, "Emergency spell", self.var_emg_spell, "200")
        self._field(box_grid, 5, "Normal spell", self.var_norm_spell, "200")
        self._field(box_grid, 6, "Cure spell", self.var_cure_spell, "200")

        io_box = section(parent, "Apply / live status")
        io_box.pack(fill="x", padx=10, pady=(4, 10))
        self.btn_apply = ctk.CTkButton(io_box, text="Apply + save", width=130)
        self.btn_apply.pack(side="left", padx=(10, 4), pady=8)
        self.lbl_msg = ctk.CTkLabel(io_box, text="", anchor="w")
        self.lbl_msg.pack(side="left", padx=12, pady=8)
        self.btn_apply.configure(command=self._on_apply)

        self._load(self.ctx.active_config())

    def _field(self, box: ctk.CTkFrame, row: int, label: str, var: object, width: str) -> None:
        ctk.CTkLabel(box, text=label, anchor="w").grid(
            row=row, column=0, sticky="w", padx=8, pady=4
        )
        ctk.CTkEntry(box, textvariable=var, width=int(width)).grid(
            row=row, column=1, sticky="w", padx=8, pady=4
        )

    def _load(self, cfg: BotConfig) -> None:
        h: HealingParams = cfg.healing
        self.var_emg.set(str(h.emergency_hp_percent))
        self.var_norm.set(str(h.normal_hp_percent))
        self.var_mana.set(str(h.mana_threshold))
        self.var_cooldown.set(str(h.heal_cooldown_ms))
        self.var_emg_spell.set(h.emergency_spell)
        self.var_norm_spell.set(h.normal_spell)
        self.var_cure_spell.set(h.cure_spell)

    def _collect(self) -> HealingParams:
        try:
            emg = float(self.var_emg.get())
            norm = float(self.var_norm.get())
            mana = float(self.var_mana.get())
            cooldown = int(self.var_cooldown.get())
        except ValueError as exc:
            raise ValueError("thresholds must be numbers") from exc
        params = HealingParams(
            emergency_hp_percent=emg,
            normal_hp_percent=norm,
            mana_threshold=mana,
            heal_cooldown_ms=cooldown,
            emergency_spell=self.var_emg_spell.get().strip(),
            normal_spell=self.var_norm_spell.get().strip(),
            cure_spell=self.var_cure_spell.get().strip(),
        )
        # Validate against the real dataclass's rules (raises ValueError).
        params.to_healing_config()
        return params

    def _on_apply(self) -> None:
        try:
            params = self._collect()
        except ValueError as exc:
            self.lbl_msg.configure(text=f"invalid: {exc}")
            return

        def mutate(cfg: BotConfig) -> None:
            cfg.healing = params

        try:
            self.ctx.config_store.update(mutate)
        except ValueError as exc:
            self.lbl_msg.configure(text=f"invalid: {exc}")
            return
        self.lbl_msg.configure(text="applied + saved")

    def on_poll(self, snap: WorldState) -> None:
        hp = snap.hp_percent
        mp = snap.mp_percent
        self.lbl_msg.configure(text=f"live HP {hp:.0f}%  MP {mp:.0f}%")
