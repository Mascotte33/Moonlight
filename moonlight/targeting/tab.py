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
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, NamedTuple

import customtkinter as ctk
from PIL import Image

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
from moonlight.targeting.targeting import TargetingConfig

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.core.state import WorldState

__all__ = [
    "MOVEMENT_LABELS",
    "OPTIONS",
    "AreaRuleFields",
    "MonsterRowFields",
    "TargetingSettings",
    "TargetingTab",
    "apply_targeting",
]

OPTIONS = [
    Option("Targeting on", "targeting.enabled", "switch"),
    Option("Client key: Attack Next (cavebot when stuck)", "loop.attack_combo", "hotkey"),
]

#: Movement menu text for each config value.
MOVEMENT_LABELS = {"follow": "chase", "none": "stand still"}


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
    enabled: bool = True


class TargetingSettings(NamedTuple):
    """The Settings card as the controls hold it."""

    attack_cooldown_ms: str
    unreachable_after_seconds: str
    toggle_chase_hotkey: str = ""


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
    others are the named rows. A row still being filled in (a monster row
    without a name, an area rule without a key) is left out until it has one.
    A turned-off targeting module keeps the values.

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
        if number == 0 or row.name.strip()
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
            "enabled": row.enabled,
        }
        for number, row in enumerate(area_rows, start=1)
        if row.hotkey.strip()
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
        toggle_chase_hotkey=settings.toggle_chase_hotkey.strip(),
    )
    return BotConfig.from_dict(data)


def _card(parent: ctk.CTkFrame, title: str, hint: str = "") -> tuple[ctk.CTkFrame, ctk.CTkFrame]:
    """A titled card; returns the card and the frame its grid rows go in."""
    box = section(parent, title)
    box.pack(fill="x", padx=10, pady=(10, 0))
    if hint:
        ctk.CTkLabel(
            box, text=hint, anchor="w", justify="left", wraplength=640, text_color=theme.TEXT_DIM
        ).pack(fill="x", padx=theme.PAD)
    rows = ctk.CTkFrame(box, fg_color="transparent")
    rows.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
    return box, rows


def _header(rows: ctk.CTkFrame, titles: Sequence[str]) -> None:
    """Column titles in grid row 0 of *rows*."""
    for column, title in enumerate(titles):
        ctk.CTkLabel(rows, text=title, anchor="w", text_color=theme.TEXT_DIM).grid(
            row=0, column=column, sticky="w", padx=(0, theme.UNIT)
        )


def _add_button(box: ctk.CTkFrame, text: str, command: Callable[[], None]) -> None:
    button = row_button(box, text, command, width=120)
    button.pack_configure(padx=theme.PAD, pady=theme.UNIT, anchor="w")


def _place(widgets: Sequence[Any], grid_row: int) -> None:
    """Put *widgets* in grid row *grid_row*, one per column."""
    for column, widget in enumerate(widgets):
        widget.grid(row=grid_row, column=column, sticky="w", padx=(0, theme.UNIT), pady=(2, 0))


def _blank_zero(percent: float) -> str:
    return "" if percent == 0.0 else f"{percent:g}"


@dataclass
class _MonsterLine:
    """The controls of one monster row."""

    name: ctk.CTkEntry | None
    priority: ctk.CTkEntry
    ignore: ctk.CTkCheckBox
    hotkey: HotkeyBox
    mana: ctk.CTkEntry
    movement: ctk.CTkOptionMenu
    widgets: list[Any]

    def fields(self) -> MonsterRowFields:
        label = self.movement.get()
        movement = next(value for value, text in MOVEMENT_LABELS.items() if text == label)
        return MonsterRowFields(
            self.name.get() if self.name is not None else "",
            self.priority.get(),
            bool(self.ignore.get()),
            self.hotkey.get(),
            self.mana.get(),
            movement,
        )


@dataclass
class _AreaLine:
    """The controls of one area rule."""

    enabled: ctk.CTkCheckBox
    hotkey: HotkeyBox
    count: ctk.CTkEntry
    mana: ctk.CTkEntry
    widgets: list[Any]

    def fields(self) -> AreaRuleFields:
        return AreaRuleFields(
            self.hotkey.get(), self.count.get(), self.mana.get(), bool(self.enabled.get())
        )

    def show_lock(self) -> None:
        """A ticked (working) rule grays its values so they are not changed by accident."""
        state = "disabled" if self.enabled.get() else "normal"
        for widget in (self.hotkey.btn, self.count, self.mana):
            widget.configure(state=state)


class TargetingTab(Tab):
    """Targeting rows, area rules and settings, saved into the picked config."""

    title = "Targeting"
    options = OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        settings_box = section(parent, "Targeting")
        settings_box.pack(fill="x", padx=10, pady=(10, 0))
        self.build_options(settings_box)
        line = ctk.CTkFrame(settings_box, fg_color="transparent")
        line.pack(fill="x", padx=theme.PAD, pady=theme.UNIT)
        ctk.CTkLabel(line, text="Attack key every (ms)", text_color=theme.TEXT_DIM).pack(
            side="left"
        )
        self._cooldown_entry = row_entry(line, "", 64, self._save)
        ctk.CTkLabel(
            line, text="Give up on a target after (s)", text_color=theme.TEXT_DIM
        ).pack(side="left", padx=(theme.PAD, 0))
        self._unreachable_entry = row_entry(line, "", 48, self._save)
        chase_line = ctk.CTkFrame(settings_box, fg_color="transparent")
        chase_line.pack(fill="x", padx=theme.PAD, pady=theme.UNIT)
        ctk.CTkLabel(
            chase_line, text="Client key: Toggle Chase Mode", text_color=theme.TEXT_DIM
        ).pack(side="left")
        self._toggle_chase_hotkey = HotkeyBox(chase_line, self._save)
        self._toggle_chase_hotkey.pack(side="left", padx=(theme.UNIT, 0))

        monsters_box, self._monsters_frame = _card(
            parent,
            "Monsters",
            "The first row is every monster without a row of its own. A higher priority "
            "is attacked first. No key = melee only. The key is not pressed below its "
            "min mana %.",
        )
        _header(
            self._monsters_frame,
            ["Monster", "Priority", "", "Attack key", "Min mana %", "Movement", ""],
        )
        _add_button(monsters_box, "+ Add monster", self._add_monster)

        area_box, self._area_frame = _card(
            parent,
            "Area spells (the first matching rule is used)",
            "Tick a rule to use it. A used rule is grayed so it is not changed by accident.",
        )
        _header(self._area_frame, ["Use", "Key", "Monsters next to you", "Min mana %", ""])
        _add_button(area_box, "+ Add area spell", self._add_area_rule)

        _seen_box, self._seen_frame = _card(
            parent,
            "Seen monsters",
            "Monsters the battle list showed that have no row yet. Check the name, then add it.",
        )
        self._seen_empty = ctk.CTkLabel(
            self._seen_frame,
            text="nothing new (the running bot adds what it sees)",
            text_color=theme.TEXT_DIM,
        )
        self._seen_empty.grid(row=0, column=0, sticky="w")
        #: Name hash -> (name box, the line's widgets) of each shown seen monster.
        self._seen_lines: dict[str, tuple[ctk.CTkEntry, list[Any]]] = {}
        self._seen_last_grid_row = 0

        self.lbl_msg = ctk.CTkLabel(parent, text="", anchor="w", text_color=theme.AMBER)
        self.lbl_msg.pack(fill="x", padx=10)
        self.lbl_live = ctk.CTkLabel(
            parent, text="", anchor="w", font=ctk.CTkFont(family=theme.MONO)
        )
        self.lbl_live.pack(fill="x", padx=10)

        self._monster_lines: list[_MonsterLine] = []
        self._area_lines: list[_AreaLine] = []
        #: Last used grid row; removed rows leave empty grid rows, which take no space.
        self._last_grid_row = 0
        self._load_rows()

    def _load_rows(self) -> None:
        """Fill every row and the settings from the picked config."""
        cfg = load_real_config()
        targeting = read_option(cfg, "targeting")
        assert isinstance(targeting, TargetingConfig)
        old_widgets = [w for line in self._monster_lines for w in line.widgets]
        old_widgets += [w for line in self._area_lines for w in line.widgets]
        for widget in old_widgets:
            widget.destroy()
        self._monster_lines, self._area_lines = [], []
        for row in (targeting.default_monster, *targeting.monsters):
            self._add_monster_line(
                MonsterRowFields(
                    row.name,
                    str(row.priority),
                    row.ignore,
                    row.hotkey,
                    _blank_zero(row.minimum_mana_percent),
                    row.movement,
                )
            )
        for rule in targeting.area_rules:
            self._add_area_line(
                AreaRuleFields(
                    rule.hotkey,
                    str(rule.minimum_adjacent_monsters),
                    _blank_zero(rule.minimum_mana_percent),
                    rule.enabled,
                )
            )
        for entry, value in (
            (self._cooldown_entry, str(targeting.attack_cooldown_ms)),
            (self._unreachable_entry, f"{targeting.unreachable_after_seconds:g}"),
        ):
            entry.delete(0, "end")
            entry.insert(0, value)
        self._toggle_chase_hotkey.set(targeting.toggle_chase_hotkey)

    def _next_grid_row(self) -> int:
        self._last_grid_row += 1
        return self._last_grid_row

    def _add_monster_line(self, row: MonsterRowFields) -> None:
        rows = self._monsters_frame
        first = not self._monster_lines
        name: ctk.CTkEntry | None = None
        name_widget: Any
        if first:
            name_widget = ctk.CTkLabel(rows, text="all others", width=120, anchor="w")
        else:
            name = name_widget = row_entry(rows, row.name, 120, self._save, pack_left=False)
        priority = row_entry(rows, row.priority, 40, self._save, pack_left=False)
        ignore = ctk.CTkCheckBox(rows, text="never attack", width=100, command=self._save)
        if row.ignore:
            ignore.select()
        hotkey = HotkeyBox(rows, self._save)
        hotkey.set(row.hotkey)
        mana = row_entry(rows, row.minimum_mana_percent, 48, self._save, pack_left=False)
        movement = ctk.CTkOptionMenu(
            rows,
            values=list(MOVEMENT_LABELS.values()),
            width=110,
            fg_color=theme.RAISED,
            button_color=theme.RAISED,
            button_hover_color=theme.HOVER,
            command=lambda _v: self._save(),
        )
        movement.set(MOVEMENT_LABELS[row.movement])
        widgets: list[Any] = [name_widget, priority, ignore, hotkey, mana, movement]
        line = _MonsterLine(name, priority, ignore, hotkey, mana, movement, widgets)
        if not first:
            widgets.append(
                row_button(rows, "✕", partial(self._delete_monster, line), pack_left=False)
            )
        _place(widgets, self._next_grid_row())
        self._monster_lines.append(line)

    def _add_area_line(self, row: AreaRuleFields) -> None:
        rows = self._area_frame
        enabled = ctk.CTkCheckBox(rows, text="", width=24)
        hotkey = HotkeyBox(rows, self._save)
        hotkey.set(row.hotkey)
        count = row_entry(rows, row.minimum_adjacent_monsters, 40, self._save, pack_left=False)
        mana = row_entry(rows, row.minimum_mana_percent, 48, self._save, pack_left=False)
        buttons = ctk.CTkFrame(rows, fg_color="transparent")
        line = _AreaLine(enabled, hotkey, count, mana, [enabled, hotkey, count, mana, buttons])
        row_button(buttons, "↑", partial(self._move_area_rule, line, -1))
        row_button(buttons, "↓", partial(self._move_area_rule, line, 1))
        row_button(buttons, "✕", partial(self._delete_area_rule, line))
        enabled.configure(command=partial(self._toggle_area_rule, line))
        if row.enabled:
            enabled.select()
        line.show_lock()
        _place(line.widgets, self._next_grid_row())
        self._area_lines.append(line)

    def _add_monster(self, name: str = "") -> None:
        """A blank named row at the bottom; it saves once it has a name."""
        self._add_monster_line(MonsterRowFields(name, "1", False, "", "", "follow"))
        if name:
            self._save()

    def _delete_monster(self, line: _MonsterLine) -> None:
        for widget in line.widgets:
            widget.destroy()
        self._monster_lines.remove(line)
        self._save()

    def _add_area_rule(self) -> None:
        """A blank, unticked rule at the bottom; it saves once it has a key."""
        self._add_area_line(AreaRuleFields("", "1", "", enabled=False))

    def _toggle_area_rule(self, line: _AreaLine) -> None:
        line.show_lock()
        self._save()

    def _move_area_rule(self, line: _AreaLine, step: int) -> None:
        index = self._area_lines.index(line)
        target = index + step
        if 0 <= target < len(self._area_lines):
            other = self._area_lines[target]
            row = int(line.enabled.grid_info()["row"])
            other_row = int(other.enabled.grid_info()["row"])
            _place(line.widgets, other_row)
            _place(other.widgets, row)
            self._area_lines[index], self._area_lines[target] = other, line
            self._save()

    def _delete_area_rule(self, line: _AreaLine) -> None:
        for widget in line.widgets:
            widget.destroy()
        self._area_lines.remove(line)
        self._save()

    def _settings(self) -> TargetingSettings:
        return TargetingSettings(
            self._cooldown_entry.get(),
            self._unreachable_entry.get(),
            self._toggle_chase_hotkey.get(),
        )

    def _save(self) -> None:
        """Save everything; a refused value shows its reason and nothing is saved."""
        try:
            self.apply()
        except ConfigError as exc:
            self.lbl_msg.configure(text=str(exc))
            return
        self.lbl_msg.configure(text="")
        self.ctx.config_changed()

    def submit(self) -> None:
        self._save()

    def apply(self) -> BotConfig:
        path = picked_config()
        cfg = load_real_config(path)
        for option in self.options:
            cfg = write_option(cfg, option, self._readers[option.field]())
        cfg = apply_targeting(
            cfg,
            [line.fields() for line in self._monster_lines],
            [line.fields() for line in self._area_lines],
            self._settings(),
        )
        cfg.save(path)
        return cfg

    def reload_options(self) -> None:
        super().reload_options()
        self._load_rows()

    def _seen_monsters(self) -> SeenMonsters | None:
        """The running bot's seen-monsters store; None while no bot is built."""
        controller = self.ctx.controller
        return controller.loop.seen_monsters if controller is not None else None

    def _add_seen_line(self, monster: SeenMonster, store: SeenMonsters) -> None:
        """One seen monster: its picture, a name box, an add and a remove button."""
        rows = self._seen_frame
        picture_path = store.image_path(monster.name_hash)
        picture_widget: Any
        if picture_path.exists():
            with Image.open(picture_path) as opened:
                picture = opened.convert("RGB")
            image = ctk.CTkImage(picture, size=picture.size)
            picture_widget = ctk.CTkLabel(rows, text="", image=image)
        else:
            picture_widget = ctk.CTkLabel(rows, text=monster.name_hash[:8], anchor="w")
        entry = row_entry(rows, monster.display_name, 140, lambda: None, pack_left=False)
        rename = partial(self._rename_seen, store, monster.name_hash, entry)
        for event in ("<Return>", "<FocusOut>"):
            entry.bind(event, lambda _e, rename=rename: rename())
        add = row_button(
            rows,
            "Add to monsters",
            partial(self._add_seen_to_monsters, store, monster.name_hash, entry),
            130,
            False,
        )
        remove = row_button(
            rows, "✕", partial(self._remove_seen, store, monster.name_hash), 28, False
        )
        widgets = [picture_widget, entry, add, remove]
        self._seen_last_grid_row += 1
        _place(widgets, self._seen_last_grid_row)
        self._seen_lines[monster.name_hash] = (entry, widgets)

    @staticmethod
    def _rename_seen(store: SeenMonsters, name_hash: str, entry: ctk.CTkEntry) -> None:
        store.rename(name_hash, entry.get().strip())

    def _add_seen_to_monsters(
        self, store: SeenMonsters, name_hash: str, entry: ctk.CTkEntry
    ) -> None:
        name = entry.get().strip()
        if not name:
            self.lbl_msg.configure(text="type the monster's name first")
            return
        store.rename(name_hash, name)
        self._add_monster(name)

    def _remove_seen(self, store: SeenMonsters, name_hash: str) -> None:
        store.forget(name_hash)
        self._drop_seen_line(name_hash)

    def _drop_seen_line(self, name_hash: str) -> None:
        for widget in self._seen_lines.pop(name_hash)[1]:
            widget.destroy()

    def _show_seen_monsters(self, store: SeenMonsters) -> None:
        """A line per seen monster that has no monster row yet."""
        names_with_a_row = {
            line.name.get().strip().lower()
            for line in self._monster_lines
            if line.name is not None
        }
        for monster in store.entries():
            name = monster.display_name.lower()
            line = self._seen_lines.get(monster.name_hash)
            if name and name in names_with_a_row:
                if line is not None:
                    self._drop_seen_line(monster.name_hash)
            elif line is None:
                self._add_seen_line(monster, store)
            elif monster.display_name and not line[0].get():
                # The guess came after the line was drawn; a typed name is never replaced.
                line[0].insert(0, monster.display_name)
        if self._seen_lines:
            self._seen_empty.grid_forget()
        else:
            self._seen_empty.grid(row=0, column=0, sticky="w")

    def on_poll(self, snap: WorldState) -> None:
        self.show_paused(["targeting.enabled"], self.ctx.paused)
        names = ", ".join(m.name or "?" for m in snap.monsters) or "none"
        text = f"creatures on screen: {names}"
        if self.lbl_live.cget("text") != text:
            self.lbl_live.configure(text=text)
        store = self._seen_monsters()
        if store is not None:
            self._show_seen_monsters(store)
