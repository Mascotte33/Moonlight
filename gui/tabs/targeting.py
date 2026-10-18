"""Targeting tab — creature rules + whitelist/blacklist (T39).

Edits :class:`~moonlight.gui.config_bridge.TargetingParams`, which mirrors the
real ``moonlight.decision.targeting.TargetingConfig`` (max monsters to
engage, prefer-low-HP, player protection) plus GUI-level whitelist/blacklist
name filters. Apply writes into the live config store and persists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui.config_bridge import BotConfig, TargetingParams
from moonlight.gui.tabs.base import Tab, section

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.state import WorldState

__all__ = ["TargetingTab"]


def _parse_int(text: str) -> int | None:
    try:
        return int(text.strip())
    except ValueError:
        return None


class TargetingTab(Tab):
    """Targeting rules + creature whitelist/blacklist, wired to
    ``decision.targeting.TargetingConfig``."""

    title = "Targeting"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)

        rules = section(parent, "Engagement rules")
        rules.pack(fill="x", padx=10, pady=(10, 4))
        rules_grid = ctk.CTkFrame(rules, fg_color="transparent")
        rules_grid.pack(fill="both", expand=True)
        rules_grid.grid_columnconfigure(1, weight=1)

        self.var_max = ctk.StringVar()
        self.var_combo = ctk.StringVar()
        self.var_prefer_low = ctk.BooleanVar()
        self.var_player_prot = ctk.BooleanVar()

        ctk.CTkLabel(rules_grid, text="Max monsters to engage", anchor="w").grid(
            row=0, column=0, sticky="w", padx=8, pady=4
        )
        self.ent_max = ctk.CTkEntry(rules_grid, textvariable=self.var_max, width=120)
        self.ent_max.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(rules_grid, text="Attack combo (loop hotkey)", anchor="w").grid(
            row=1, column=0, sticky="w", padx=8, pady=4
        )
        self.ent_combo = ctk.CTkEntry(rules_grid, textvariable=self.var_combo, width=120)
        self.ent_combo.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        self.chk_prefer_low = ctk.CTkCheckBox(
            rules_grid, text="Prefer lowest-HP monster", variable=self.var_prefer_low
        )
        self.chk_prefer_low.grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        self.chk_player_prot = ctk.CTkCheckBox(
            rules_grid,
            text="Player protection (flee when a player is on screen)",
            variable=self.var_player_prot,
        )
        self.chk_player_prot.grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=2)

        # -- creature name filters ------------------------------------------
        filters = section(parent, "Creature name filters")
        filters.pack(fill="both", expand=True, padx=10, pady=4)
        filters_grid = ctk.CTkFrame(filters, fg_color="transparent")
        filters_grid.pack(fill="both", expand=True)
        filters_grid.grid_columnconfigure(0, weight=1)
        filters_grid.grid_columnconfigure(1, weight=1)
        filters_grid.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            filters_grid, text="Whitelist (engage only these; empty = all)", anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(filters_grid, text="Blacklist (never engage these)", anchor="w").grid(
            row=0, column=1, sticky="w", padx=8, pady=4
        )
        self.txt_whitelist = ctk.CTkTextbox(filters_grid)
        self.txt_whitelist.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=4)
        self.txt_blacklist = ctk.CTkTextbox(filters_grid)
        self.txt_blacklist.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=4)

        io_box = section(parent, "Apply / live creatures")
        io_box.pack(fill="x", padx=10, pady=(4, 10))
        self.btn_apply = ctk.CTkButton(io_box, text="Apply + save", width=130)
        self.btn_apply.pack(side="left", padx=(10, 4), pady=8)
        self.lbl_msg = ctk.CTkLabel(io_box, text="", anchor="w")
        self.lbl_msg.pack(side="left", padx=12, pady=8)
        self.btn_apply.configure(command=self._on_apply)
        self._load(self.ctx.active_config())

    def _load(self, cfg: BotConfig) -> None:
        t: TargetingParams = cfg.targeting
        self.var_max.set(str(t.max_monsters_to_engage))
        self.var_combo.set(t.attack_combo)
        self.var_prefer_low.set(t.prefer_low_hp)
        self.var_player_prot.set(t.player_protection)
        self._set_list(self.txt_whitelist, t.whitelist)
        self._set_list(self.txt_blacklist, t.blacklist)

    @staticmethod
    def _set_list(textbox: ctk.CTkTextbox, names: list[str]) -> None:
        textbox.configure(state="normal")
        textbox.delete("1.0", "end")
        textbox.insert("end", "\n".join(names))
        textbox.configure(state="disabled")

    @staticmethod
    def _read_list(textbox: ctk.CTkTextbox) -> list[str]:
        raw = textbox.get("1.0", "end").strip()
        if not raw:
            return []
        items = [line.strip() for line in raw.splitlines()]
        return [item for item in items if item]

    def _collect(self) -> TargetingParams:
        max_mon = _parse_int(self.var_max.get())
        if max_mon is None or max_mon < 1:
            raise ValueError("max monsters must be an integer >= 1")
        combo = self.var_combo.get().strip()
        if not combo:
            raise ValueError("attack combo must be non-empty")
        params = TargetingParams(
            max_monsters_to_engage=max_mon,
            prefer_low_hp=bool(self.var_prefer_low.get()),
            player_protection=bool(self.var_player_prot.get()),
            attack_combo=combo,
            whitelist=self._read_list(self.txt_whitelist),
            blacklist=self._read_list(self.txt_blacklist),
        )
        # Validate against the real dataclass (raises ValueError).
        params.to_targeting_config()
        return params

    def _on_apply(self) -> None:
        try:
            params = self._collect()
        except ValueError as exc:
            self.lbl_msg.configure(text=f"invalid: {exc}")
            return

        def mutate(cfg: BotConfig) -> None:
            cfg.targeting = params

        try:
            self.ctx.config_store.update(mutate)
        except ValueError as exc:
            self.lbl_msg.configure(text=f"invalid: {exc}")
            return
        self.lbl_msg.configure(text="applied + saved")

    def on_poll(self, snap: WorldState) -> None:
        names = ", ".join(m.name for m in snap.monsters) or "none"
        self.lbl_msg.configure(text=f"creatures on screen: {names}")
