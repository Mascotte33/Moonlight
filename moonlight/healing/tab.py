"""Healing tab: the ordered rules, the cures, the cooldowns and the live HP/MP label.

# doc: docs/prs/healing.md#healing-rules
# doc: docs/prs/healing.md#cures

Every change saves at once into the picked config, like the other tabs. A
value the config refuses shows its reason in the message label and is not
saved. :func:`apply_healing` is the pure row-to-config step.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from functools import partial
from typing import TYPE_CHECKING, NamedTuple

import customtkinter as ctk

from moonlight.core.config import BotConfig, ConfigError
from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config, picked_config
from moonlight.gui.tab_base import (
    HotkeyBox,
    Option,
    Tab,
    read_option,
    row_button,
    row_entry,
    section,
)
from moonlight.healing.healing import CONDITIONS, CureRule, HealingConfig, HealingRule

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.core.state import WorldState

__all__ = ["OPTIONS", "CureRow", "HealingTab", "RuleRow", "apply_healing"]

OPTIONS = [
    Option("Spell cooldown (ms)", "healing.spell_cooldown_ms", "slider", min=100, max=2000),
    Option("Potion cooldown (ms)", "healing.potion_cooldown_ms", "slider", min=100, max=2000),
]
_KINDS = ["spell", "potion"]


class RuleRow(NamedTuple):
    """One rule row as the controls hold it: text, percents may be blank (= unused)."""

    kind: str
    hotkey: str
    hp_below_percent: str
    mp_below_percent: str
    minimum_mana_percent: str


class CureRow(NamedTuple):
    """One cure row as the controls hold it."""

    condition: str
    enabled: bool
    hotkey: str


def _percent(text: str, where: str) -> float:
    if text.strip() == "":
        return 0.0
    try:
        return float(text)
    except ValueError:
        raise ConfigError(f"{where}: {text!r} is not a number") from None


def apply_healing(
    cfg: BotConfig,
    rules: Sequence[RuleRow],
    cures: Sequence[CureRow],
    spell_cooldown_ms: int,
    potion_cooldown_ms: int,
) -> BotConfig:
    """*cfg* with its healing section set from the tab's rows.

    A turned-off healing module keeps the values. A cure with no hotkey that
    is off is left out; one that is on is refused.

    Raises:
        ConfigError: For a number that is not one, or any value the config refuses.
    """
    rule_values = tuple(
        HealingRule(
            row.kind,
            row.hotkey.strip(),
            _percent(row.hp_below_percent, f"healing rule {number} HP"),
            _percent(row.mp_below_percent, f"healing rule {number} MP"),
            _percent(row.minimum_mana_percent, f"healing rule {number} minimum mana"),
        )
        for number, row in enumerate(rules, start=1)
    )
    cure_values = tuple(
        CureRule(row.condition, row.enabled, row.hotkey.strip())
        for row in cures
        if row.enabled or row.hotkey.strip()
    )
    healing = HealingConfig(rule_values, cure_values, spell_cooldown_ms, potion_cooldown_ms)
    data = cfg.to_dict()
    data["modules"]["healing"].update(
        rules=[asdict(r) for r in healing.rules],  # from_dict reads lists, not tuples
        cures=[asdict(c) for c in healing.cures],
        spell_cooldown_ms=healing.spell_cooldown_ms,
        potion_cooldown_ms=healing.potion_cooldown_ms,
    )
    return BotConfig.from_dict(data)




class HealingTab(Tab):
    """Healing rules, cures and cooldowns, saved into the picked config."""

    title = "Healing"
    options = OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        rules_box = section(parent, "Rules (checked top to bottom)")
        rules_box.pack(fill="x", padx=10, pady=(10, 0))
        ctk.CTkLabel(
            rules_box,
            text="A rule presses its key when HP or MP is at or below its %. "
            "Blank % = unused. Min mana % stops it below that MP.",
            anchor="w",
            justify="left",
            wraplength=640,
            text_color=theme.TEXT_DIM,
        ).pack(fill="x", padx=theme.PAD)
        self._rules_frame = ctk.CTkFrame(rules_box, fg_color="transparent")
        self._rules_frame.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        row_button(rules_box, "Add rule", self._add_rule).configure(width=80)
        rules_box.winfo_children()[-1].pack_configure(padx=theme.PAD, pady=theme.UNIT, anchor="w")

        cures_box = section(parent, "Cures (spell slot, after the rules)")
        cures_box.pack(fill="x", padx=10, pady=(10, 0))
        self._cures_frame = ctk.CTkFrame(cures_box, fg_color="transparent")
        self._cures_frame.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, theme.UNIT))

        cooldowns_box = section(parent, "Cooldowns")
        cooldowns_box.pack(fill="x", padx=10, pady=(10, 0))
        self.build_options(cooldowns_box, OPTIONS)

        self.lbl_msg = ctk.CTkLabel(parent, text="", anchor="w", text_color=theme.AMBER)
        self.lbl_msg.pack(fill="x", padx=10)
        self.lbl_live = ctk.CTkLabel(
            parent, text="", anchor="w", font=ctk.CTkFont(family=theme.MONO)
        )
        self.lbl_live.pack(fill="x", padx=10)

        self._rule_widgets: list[tuple[ctk.CTkOptionMenu, HotkeyBox, list[ctk.CTkEntry]]] = []
        self._cure_widgets: list[tuple[str, ctk.CTkCheckBox, HotkeyBox]] = []
        self._load_rows()

    def _load_rows(self) -> None:
        """Fill the rule and cure rows from the picked config."""
        cfg = load_real_config()
        rules = read_option(cfg, "healing.rules")
        cures = read_option(cfg, "healing.cures")
        assert isinstance(rules, tuple) and isinstance(cures, tuple)

        def blank_zero(percent: float) -> str:
            return "" if percent == 0.0 else f"{percent:g}"

        self._draw_rules(
            [
                RuleRow(
                    r.kind,
                    r.hotkey,
                    blank_zero(r.hp_below_percent),
                    blank_zero(r.mp_below_percent),
                    blank_zero(r.minimum_mana_percent),
                )
                for r in rules
            ]
        )
        by_condition = {c.condition: c for c in cures}
        self._draw_cures(
            [
                CureRow(name, by_condition[name].enabled, by_condition[name].hotkey)
                if name in by_condition
                else CureRow(name, False, "")
                for name in CONDITIONS
            ]
        )

    def _rule_rows(self) -> list[RuleRow]:
        return [
            RuleRow(kind.get(), hotkey.get(), *(e.get() for e in entries))
            for kind, hotkey, entries in self._rule_widgets
        ]

    def _cure_rows(self) -> list[CureRow]:
        return [
            CureRow(name, bool(check.get()), hotkey.get())
            for name, check, hotkey in self._cure_widgets
        ]

    def _draw_rules(self, rows: Sequence[RuleRow]) -> None:
        for child in self._rules_frame.winfo_children():
            child.destroy()
        self._rule_widgets = []
        for index, row in enumerate(rows):
            line = ctk.CTkFrame(self._rules_frame, fg_color="transparent")
            line.pack(fill="x", pady=(theme.UNIT, 0))
            kind = ctk.CTkOptionMenu(
                line,
                values=_KINDS,
                width=90,
                fg_color=theme.RAISED,
                button_color=theme.RAISED,
                button_hover_color=theme.HOVER,
                command=lambda _v: self._save(),
            )
            kind.set(row.kind)
            kind.pack(side="left")
            hotkey = HotkeyBox(line, self._save)
            hotkey.set(row.hotkey)
            hotkey.pack(side="left", padx=(theme.UNIT, 0))
            entries = []
            for label, value in (
                ("HP <", row.hp_below_percent),
                ("MP <", row.mp_below_percent),
                ("min mana", row.minimum_mana_percent),
            ):
                ctk.CTkLabel(line, text=label, text_color=theme.TEXT_DIM).pack(
                    side="left", padx=(theme.UNIT, 0)
                )
                entries.append(row_entry(line, value, 48, self._save))
            row_button(line, "↑", partial(self._move_rule, index, -1))
            row_button(line, "↓", partial(self._move_rule, index, 1))
            row_button(line, "✕", partial(self._delete_rule, index))
            self._rule_widgets.append((kind, hotkey, entries))

    def _draw_cures(self, rows: Sequence[CureRow]) -> None:
        for child in self._cures_frame.winfo_children():
            child.destroy()
        self._cure_widgets = []
        for row in rows:
            line = ctk.CTkFrame(self._cures_frame, fg_color="transparent")
            line.pack(fill="x", pady=(theme.UNIT, 0))
            check = ctk.CTkCheckBox(line, text=row.condition, width=120, command=self._save)
            if row.enabled:
                check.select()
            check.pack(side="left")
            hotkey = HotkeyBox(line, self._save)
            hotkey.set(row.hotkey)
            hotkey.pack(side="left")
            self._cure_widgets.append((row.condition, check, hotkey))

    def _add_rule(self) -> None:
        """A blank spell rule at the bottom; it saves once it has a key and a trigger."""
        self._draw_rules([*self._rule_rows(), RuleRow("spell", "", "", "", "")])

    def _move_rule(self, index: int, step: int) -> None:
        rows = self._rule_rows()
        target = index + step
        if 0 <= target < len(rows):
            rows[index], rows[target] = rows[target], rows[index]
            self._draw_rules(rows)
            self._save()

    def _delete_rule(self, index: int) -> None:
        rows = self._rule_rows()
        del rows[index]
        self._draw_rules(rows)
        self._save()

    def _save(self) -> None:
        """Save everything; a refused value shows its reason and nothing is saved."""
        try:
            self.apply()
        except ConfigError as exc:
            self.lbl_msg.configure(text=str(exc))
            return
        self.lbl_msg.configure(text="")
        self.ctx.config_changed()

    def apply(self) -> BotConfig:
        path = picked_config()
        cfg = apply_healing(
            load_real_config(path),
            self._rule_rows(),
            self._cure_rows(),
            int(str(self._readers["healing.spell_cooldown_ms"]())),
            int(str(self._readers["healing.potion_cooldown_ms"]())),
        )
        cfg.save(path)
        return cfg

    def reload_options(self) -> None:
        super().reload_options()
        self._load_rows()

    def on_poll(self, snap: WorldState) -> None:
        text = f"live HP {snap.hp_percent:.0f}%  MP {snap.mp_percent:.0f}%"
        if self.lbl_live.cget("text") != text:
            self.lbl_live.configure(text=text)
