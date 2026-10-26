"""Targeting tab: monster rows, seen monsters, area rules and the settings.

# doc: docs/prs/targeting.md#monster-rows
# doc: docs/prs/targeting.md#area-rules
# doc: docs/prs/targeting.md#seen-monsters

Every change saves at once into the picked config, like the other tabs. A
value the config refuses shows its reason in the message label and is not
saved. :func:`apply_targeting` is the pure row-to-config step.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
    write_option,
)
from moonlight.targeting.seen_monsters import SeenMonster, SeenMonsters
from moonlight.targeting.targeting import MOVEMENTS, TargetingConfig

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.core.state import WorldState

__all__ = [
    "OPTIONS",
    "AreaRuleFields",
    "MonsterRowFields",
    "TargetingSettings",
    "TargetingTab",
    "apply_targeting",
]

OPTIONS = [Option("Attack hotkey (cavebot)", "loop.attack_combo", "hotkey")]


class MonsterRowFields(NamedTuple):
    """One monster row as the controls hold it: numbers are text."""

    name: str
    priority: str
    ignore: bool
    hotkey: str
    minimum_mana_percent: str
    movement: str


class AreaRuleFields(NamedTuple):
    """One area rule as the controls hold it."""

    hotkey: str
    minimum_adjacent_monsters: str
    minimum_mana_percent: str


class TargetingSettings(NamedTuple):
    """The Settings card as the controls hold it."""

    attack_cooldown_ms: str
    unreachable_after_seconds: str


def _number(text: str, cast: Callable[[str], float], where: str) -> float:
    """*text* through *cast* (blank = 0); a ConfigError that names *where* when it is no number."""
    if text.strip() == "":
        return 0
    try:
        return cast(text.strip())
    except ValueError:
        raise ConfigError(f"{where}: {text!r} is not a number") from None


def apply_targeting(
    cfg: BotConfig,
    monster_rows: Sequence[MonsterRowFields],
    area_rows: Sequence[AreaRuleFields],
    settings: TargetingSettings,
) -> BotConfig:
    """*cfg* with its targeting section set from the tab's rows.

    The first monster row is ``default_monster`` (its name is ignored); the
    others are the named rows. A turned-off targeting module keeps the values.

    Raises:
        ConfigError: For a number that is not one, or any value the config refuses.
    """
    monster_dicts = [
        {
            "name": row.name.strip() if number > 0 else "",
            "priority": _number(row.priority, int, f"monster row {number} priority"),
            "ignore": row.ignore,
            "hotkey": row.hotkey.strip(),
            "minimum_mana_percent": _number(
                row.minimum_mana_percent, float, f"monster row {number} minimum mana"
            ),
            "movement": row.movement,
        }
        for number, row in enumerate(monster_rows)
    ]
    area_dicts = [
        {
            "hotkey": row.hotkey.strip(),
            "minimum_adjacent_monsters": _number(
                row.minimum_adjacent_monsters, int, f"area rule {number} adjacent monsters"
            ),
            "minimum_mana_percent": _number(
                row.minimum_mana_percent, float, f"area rule {number} minimum mana"
            ),
        }
        for number, row in enumerate(area_rows, start=1)
    ]
    data = cfg.to_dict()
    data["modules"]["targeting"].update(
        monsters=monster_dicts[1:],
        default_monster=monster_dicts[0],
        area_rules=area_dicts,
        attack_cooldown_ms=_number(settings.attack_cooldown_ms, int, "attack cooldown"),
        unreachable_after_seconds=_number(
            settings.unreachable_after_seconds, float, "unreachable time"
        ),
    )
    return BotConfig.from_dict(data)




def _label(parent: ctk.CTkFrame, text: str) -> None:
    ctk.CTkLabel(parent, text=text, text_color=theme.TEXT_DIM).pack(
        side="left", padx=(theme.UNIT, 0)
    )


def _card(parent: ctk.CTkFrame, title: str, hint: str = "") -> tuple[ctk.CTkFrame, ctk.CTkFrame]:
    """A titled card; returns the card and the frame its rows go in."""
    box = section(parent, title)
    box.pack(fill="x", padx=10, pady=(10, 0))
    if hint:
        ctk.CTkLabel(
            box, text=hint, anchor="w", justify="left", wraplength=640, text_color=theme.TEXT_DIM
        ).pack(fill="x", padx=theme.PAD)
    rows = ctk.CTkFrame(box, fg_color="transparent")
    rows.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
    return box, rows


def _add_button(box: ctk.CTkFrame, command: Callable[[], None]) -> None:
    button = row_button(box, "Add row", command, width=80)
    button.pack_configure(padx=theme.PAD, pady=theme.UNIT, anchor="w")


class TargetingTab(Tab):
    """Targeting rows, area rules and settings, saved into the picked config."""

    title = "Targeting"
    options = OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        monsters_box, self._monsters_frame = _card(
            parent,
            "Monsters (first row = every other monster)",
            "Priority 1-5: a higher number is attacked first. Blank key = melee only. "
            "Min mana % stops the key below that MP.",
        )
        _add_button(monsters_box, self._add_monster)
        _seen_box, self._seen_frame = _card(
            parent,
            "Seen monsters",
            "Names read from the battle list. Type the real name, then Add row.",
        )
        self._seen_drawn: list[tuple[str, str]] | None = None
        area_box, self._area_frame = _card(
            parent,
            "Area rules (checked top to bottom)",
            "A rule presses its key when this many monsters stand next to you.",
        )
        _add_button(area_box, self._add_area_rule)

        settings_box = section(parent, "Settings")
        settings_box.pack(fill="x", padx=10, pady=(10, 0))
        self.build_options(settings_box)
        line = ctk.CTkFrame(settings_box, fg_color="transparent")
        line.pack(fill="x", padx=theme.PAD, pady=theme.UNIT)
        _label(line, "Attack cooldown (ms)")
        self._cooldown_entry = row_entry(line, "", 64, self._save)
        _label(line, "Unreachable after (s)")
        self._unreachable_entry = row_entry(line, "", 48, self._save)

        self.lbl_msg = ctk.CTkLabel(parent, text="", anchor="w", text_color=theme.AMBER)
        self.lbl_msg.pack(fill="x", padx=10)
        self.lbl_live = ctk.CTkLabel(
            parent, text="", anchor="w", font=ctk.CTkFont(family=theme.MONO)
        )
        self.lbl_live.pack(fill="x", padx=10)

        self._monster_widgets: list[
            tuple[ctk.CTkEntry | None, ctk.CTkEntry, ctk.CTkCheckBox, HotkeyBox, ctk.CTkEntry,
                  ctk.CTkOptionMenu]
        ] = []
        self._area_widgets: list[tuple[HotkeyBox, ctk.CTkEntry, ctk.CTkEntry]] = []
        self._load_rows()

    def _load_rows(self) -> None:
        """Fill every row and the settings from the picked config."""
        cfg = load_real_config()
        targeting = read_option(cfg, "targeting")
        assert isinstance(targeting, TargetingConfig)

        def blank_zero(percent: float) -> str:
            return "" if percent == 0.0 else f"{percent:g}"

        self._draw_monsters(
            [
                MonsterRowFields(
                    row.name,
                    str(row.priority),
                    row.ignore,
                    row.hotkey,
                    blank_zero(row.minimum_mana_percent),
                    row.movement,
                )
                for row in (targeting.default_monster, *targeting.monsters)
            ]
        )
        self._draw_area_rules(
            [
                AreaRuleFields(
                    rule.hotkey,
                    str(rule.minimum_adjacent_monsters),
                    blank_zero(rule.minimum_mana_percent),
                )
                for rule in targeting.area_rules
            ]
        )
        for entry, value in (
            (self._cooldown_entry, str(targeting.attack_cooldown_ms)),
            (self._unreachable_entry, f"{targeting.unreachable_after_seconds:g}"),
        ):
            entry.delete(0, "end")
            entry.insert(0, value)

    def _monster_rows(self) -> list[MonsterRowFields]:
        return [
            MonsterRowFields(
                name.get() if name is not None else "",
                priority.get(),
                bool(ignore.get()),
                hotkey.get(),
                mana.get(),
                movement.get(),
            )
            for name, priority, ignore, hotkey, mana, movement in self._monster_widgets
        ]

    def _area_rows(self) -> list[AreaRuleFields]:
        return [
            AreaRuleFields(hotkey.get(), count.get(), mana.get())
            for hotkey, count, mana in self._area_widgets
        ]

    def _settings(self) -> TargetingSettings:
        return TargetingSettings(
            self._cooldown_entry.get(),
            self._unreachable_entry.get(),
        )

    def _draw_monsters(self, rows: Sequence[MonsterRowFields]) -> None:
        for child in self._monsters_frame.winfo_children():
            child.destroy()
        self._monster_widgets = []
        for index, row in enumerate(rows):
            line = ctk.CTkFrame(self._monsters_frame, fg_color="transparent")
            line.pack(fill="x", pady=(theme.UNIT, 0))
            name: ctk.CTkEntry | None = None
            if index == 0:
                ctk.CTkLabel(line, text="everything else", width=110, anchor="w").pack(
                    side="left"
                )
            else:
                name = row_entry(line, row.name, 110, self._save)
            _label(line, "prio")
            priority = row_entry(line, row.priority, 28, self._save)
            ignore = ctk.CTkCheckBox(line, text="ignore", width=70, command=self._save)
            if row.ignore:
                ignore.select()
            ignore.pack(side="left", padx=(theme.UNIT, 0))
            hotkey = HotkeyBox(line, self._save)
            hotkey.set(row.hotkey)
            hotkey.pack(side="left", padx=(theme.UNIT, 0))
            _label(line, "min mana")
            mana = row_entry(line, row.minimum_mana_percent, 40, self._save)
            movement = ctk.CTkOptionMenu(
                line,
                values=list(MOVEMENTS),
                width=110,
                fg_color=theme.RAISED,
                button_color=theme.RAISED,
                button_hover_color=theme.HOVER,
                command=lambda _v: self._save(),
            )
            movement.set(row.movement)
            movement.pack(side="left", padx=(theme.UNIT, 0))
            if index > 0:
                row_button(line, "✕", partial(self._delete_monster, index))
            self._monster_widgets.append((name, priority, ignore, hotkey, mana, movement))

    def _draw_area_rules(self, rows: Sequence[AreaRuleFields]) -> None:
        for child in self._area_frame.winfo_children():
            child.destroy()
        self._area_widgets = []
        for index, row in enumerate(rows):
            line = ctk.CTkFrame(self._area_frame, fg_color="transparent")
            line.pack(fill="x", pady=(theme.UNIT, 0))
            hotkey = HotkeyBox(line, self._save)
            hotkey.set(row.hotkey)
            hotkey.pack(side="left")
            _label(line, "monsters >=")
            count = row_entry(line, row.minimum_adjacent_monsters, 28, self._save)
            _label(line, "min mana")
            mana = row_entry(line, row.minimum_mana_percent, 40, self._save)
            row_button(line, "↑", partial(self._move_area_rule, index, -1))
            row_button(line, "↓", partial(self._move_area_rule, index, 1))
            row_button(line, "✕", partial(self._delete_area_rule, index))
            self._area_widgets.append((hotkey, count, mana))

    def _add_monster(self, name: str = "") -> None:
        """A blank named row at the bottom; it saves once it has a name."""
        blank = MonsterRowFields(name, "1", False, "", "", "follow")
        self._draw_monsters([*self._monster_rows(), blank])
        if name:
            self._save()

    def _delete_monster(self, index: int) -> None:
        rows = self._monster_rows()
        del rows[index]
        self._draw_monsters(rows)
        self._save()

    def _add_area_rule(self) -> None:
        """A blank rule at the bottom; it saves once it has a key."""
        self._draw_area_rules([*self._area_rows(), AreaRuleFields("", "1", "")])

    def _move_area_rule(self, index: int, step: int) -> None:
        rows = self._area_rows()
        target = index + step
        if 0 <= target < len(rows):
            rows[index], rows[target] = rows[target], rows[index]
            self._draw_area_rules(rows)
            self._save()

    def _delete_area_rule(self, index: int) -> None:
        rows = self._area_rows()
        del rows[index]
        self._draw_area_rules(rows)
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
        cfg = load_real_config(path)
        for option in self.options:
            cfg = write_option(cfg, option, self._readers[option.field]())
        cfg = apply_targeting(cfg, self._monster_rows(), self._area_rows(), self._settings())
        cfg.save(path)
        return cfg

    def reload_options(self) -> None:
        super().reload_options()
        self._load_rows()

    def _seen_monsters(self) -> SeenMonsters | None:
        """The running bot's seen-monsters store; None while no bot is built."""
        controller = self.ctx.controller
        return controller.loop.seen_monsters if controller is not None else None

    def _draw_seen(self, entries: Sequence[SeenMonster], store: SeenMonsters) -> None:
        for child in self._seen_frame.winfo_children():
            child.destroy()
        if not entries:
            ctk.CTkLabel(
                self._seen_frame, text="none yet (start the bot)", text_color=theme.TEXT_DIM
            ).pack(anchor="w")
        for monster in entries:
            line = ctk.CTkFrame(self._seen_frame, fg_color="transparent")
            line.pack(fill="x", pady=(theme.UNIT, 0))
            ctk.CTkLabel(
                line,
                text=f"{monster.name_hash[:8]}  guess: {monster.guessed_name or '?'}",
                width=230,
                anchor="w",
                font=ctk.CTkFont(family=theme.MONO),
            ).pack(side="left")
            entry = row_entry(line, monster.name, 130, lambda: None)
            rename = partial(self._rename_seen, store, monster.name_hash, entry)
            for event in ("<Return>", "<FocusOut>"):
                entry.bind(event, lambda _e, rename=rename: rename())
            row_button(
                line,
                "Add row",
                partial(self._add_seen_to_monsters, monster.guessed_name, entry),
                width=80,
            )

    @staticmethod
    def _rename_seen(store: SeenMonsters, name_hash: str, entry: ctk.CTkEntry) -> None:
        store.rename(name_hash, entry.get().strip())

    def _add_seen_to_monsters(self, guessed_name: str, entry: ctk.CTkEntry) -> None:
        name = entry.get().strip() or guessed_name
        if name:
            self._add_monster(name)

    def on_poll(self, snap: WorldState) -> None:
        names = ", ".join(m.name for m in snap.monsters) or "none"
        text = f"creatures on screen: {names}"
        if self.lbl_live.cget("text") != text:
            self.lbl_live.configure(text=text)
        store = self._seen_monsters()
        if store is None:
            return
        entries = store.entries()
        drawn = [(m.name_hash, m.guessed_name) for m in entries]
        # ponytail: a redraw loses half-typed text; only redraw when a hash or guess is new.
        if drawn != self._seen_drawn:
            self._seen_drawn = drawn
            self._draw_seen(entries, store)
