"""Cavebot tab: Waypoints, and a Refill / Loot / Edit route switch (T3.F1.K1, T3.F7.K1, T3.F8.K1).

Routes are the bot's own files, ``<routes_dir>/<name>.json``, read by
:class:`RouteFileLoader`. ``cavebot.route`` is saved by
name, so its dropdown holds names. Picking a route loads it into
the editor; waypoints are added by hand or recorded while walking (T3.F9.K1)
and reach the route file only on Save.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from tkinter import messagebox
from typing import Any

import customtkinter as ctk

from moonlight.cavebot.cavebot import (
    ACTIONS,
    NO_TILE,
    Route,
    RouteError,
    RouteFileLoader,
    Waypoint,
)
from moonlight.cavebot.recorder import (
    StandTracker,
    cursor_line,
    make_waypoint,
    position_line,
    route_path,
    save_route,
    should_add,
    should_stop_recording,
    stairs_waypoint,
)
from moonlight.core.config import BotConfig, ConfigError
from moonlight.core.state import Position, WorldState
from moonlight.gui import loop_factory, theme
from moonlight.gui.loop_factory import load_real_config, picked_config
from moonlight.gui.settings_tab import plain_reason
from moonlight.gui.tab_base import Option, Tab, read_option, section, write_option

__all__ = [
    "LOOT_OPTIONS",
    "OPTIONS",
    "REFILL_OPTIONS",
    "ROUTE",
    "SWITCH",
    "CavebotTab",
    "apply_cavebot",
    "picked_route",
    "picked_waypoint",
    "refill_now",
    "drop_route",
    "route_names",
    "waypoint_line",
    "waypoint_table",
]

NO_ROUTES = "No routes"
PICK = "Pick a route"
NO_ROUTES_HINT = "No routes yet. Press New, then add waypoints in Edit route."
#: The cavebot's only on/off switch (Settings has none for it).
SWITCH = Option("Cavebot on", "cavebot.enabled", "switch")
ROUTE = Option("Route", "cavebot.route", "dropdown")
#: Waypoint buttons, one row each: walking, changing floor, jumping, town.
#: Stairs are walked over like any tile, so they get no button.
KIND_ROWS = (ACTIONS[:4], ACTIONS[4:7], ACTIONS[8:11], ACTIONS[11:])
#: Shown under the position while the minimap matches no map tile.
LOST_HINT = "No map match. Use minimap zoom 1 or 2, or explore the area first."
#: Direction pad: (arrow, dx, dy) in 3x3 order. Up is north, y - 1.
DIRECTIONS = (
    ("↖", -1, -1),
    ("↑", 0, -1),
    ("↗", 1, -1),
    ("←", -1, 0),
    ("•", 0, 0),
    ("→", 1, 0),
    ("↙", -1, 1),
    ("↓", 0, 1),
    ("↘", 1, 1),
)
#: Shown 1-based; ``cavebot.start_index`` is 0-based.
START = Option("Start from waypoint", "cavebot.start_index", "entry")
OPTIONS = [
    SWITCH,
    ROUTE,
    START,
    Option("Rope hotkey", "cavebot.rope_hotkey", "hotkey"),
    Option("Shovel hotkey", "cavebot.shovel_hotkey", "hotkey"),
]
LOOT_OPTIONS = [
    Option("Loot hotkey", "looting.loot_hotkey", "hotkey"),
    Option("Wait after looting (ms)", "looting.wait_ms", "entry"),
    Option("Loot only at HP % or above", "looting.min_hp_to_loot", "entry"),
]
REFILL_OPTIONS = [
    Option("Start refill at used cap %", "refill.trigger_cap", "entry"),
]


def route_names(routes_dir: Path) -> list[str]:
    """The route names in *routes_dir* (its ``*.json`` file names), sorted."""
    return sorted(p.stem for p in Path(routes_dir).glob("*.json"))


def picked_route(cfg: BotConfig) -> str:
    """The name of *cfg*'s cavebot route, or "" when it has none."""
    route = read_option(cfg, ROUTE.field)
    return route.name if isinstance(route, Route) else ""


def drop_route(cfg: BotConfig, name: str) -> BotConfig:
    """*cfg* without route *name*, so the route file can be deleted.

    A cavebot on *name* moves to the first other route on disk, from its
    first waypoint, or turns off (refill with it) when none is left.
    """
    if picked_route(cfg) != name:
        return cfg
    data = cfg.to_dict()
    modules = data["modules"]
    left = [n for n in route_names(cfg.routes_dir) if n != name]
    if left:
        modules["cavebot"].update(route=left[0], start_index=0)
    else:
        modules["cavebot"] = {"enabled": False}
        modules["refill"]["enabled"] = False
    return BotConfig.from_dict(data)


def picked_waypoint(line: int, count: int) -> int | None:
    """The waypoint (0-based) on *line* of :func:`waypoint_table`; line 1 is the header."""
    return line - 2 if 2 <= line < count + 2 else None


def waypoint_table(route: Route | None, current: int | None = None) -> str:
    """*route*'s waypoints as a text table, row *current* (0-based) marked ``>``."""
    if route is None or not route.waypoints:
        return "No waypoints loaded"
    lines = [f" {'#':>3}  {'Type':<7} {'Coordinates':<19} Label"]
    for i, wp in enumerate(route.waypoints):
        mark = ">" if i == current else " "
        where = "" if wp.action in NO_TILE else f"({wp.x}, {wp.y}, {wp.z})"
        lines.append(f"{mark}{i + 1:>3}  {wp.action:<7} {where:<19} {wp.label}".rstrip())
    return "\n".join(lines)


def waypoint_line(current: int | None, total: int) -> str:
    """ "Waypoint N of M" for 0-based *current*, or "Not running" for None."""
    return "Not running" if current is None else f"Waypoint {current + 1} of {total}"


def apply_cavebot(cfg: BotConfig, values: dict[str, object]) -> BotConfig:
    """*cfg* with the cavebot, loot and refill option *values*.

    The cavebot route is written first. With no route on disk picked the
    cavebot options are skipped, so Save still works for an off cavebot
    without routes. A blank cavebot hotkey keeps its default. The start
    waypoint is given 1-based. The switch
    goes after the route, so turning the cavebot on with no route is refused.

    Raises:
        ConfigError: For a refused value.
    """
    if values.get(ROUTE.field) in route_names(cfg.routes_dir):
        start = values.get(START.field, "")
        if start != "":
            try:
                values = {**values, START.field: int(str(start)) - 1}
            except ValueError:
                raise ConfigError(f"start waypoint {start!r} is not a whole number") from None
        for option in sorted(OPTIONS, key=lambda o: o is not ROUTE):
            if option is not SWITCH and values.get(option.field, "") != "":
                cfg = write_option(cfg, option, values[option.field])
    if SWITCH.field in values:
        cfg = write_option(cfg, SWITCH, values[SWITCH.field])
    for option in LOOT_OPTIONS + REFILL_OPTIONS:
        if option.field in values:
            cfg = write_option(cfg, option, values[option.field])
    return cfg


#: Widgets ``_lock`` grays out while Cavebot is on.
CONTROLS = (
    ctk.CTkButton,
    ctk.CTkCheckBox,
    ctk.CTkComboBox,
    ctk.CTkEntry,
    ctk.CTkOptionMenu,
    ctk.CTkSlider,
    ctk.CTkSwitch,
)


def _menu(box: ctk.CTkFrame, options: list[Option], option: Option) -> ctk.CTkOptionMenu:
    """The dropdown :meth:`Tab.build_options` made for *option* in *box*."""
    return next(w for w in _row(box, options, option) if isinstance(w, ctk.CTkOptionMenu))


def _row(box: ctk.CTkFrame, options: list[Option], option: Option) -> list[object]:
    """The widgets in the row :meth:`Tab.build_options` made for *option*."""
    rows = [w for w in box.winfo_children() if isinstance(w, ctk.CTkFrame)]
    return list(rows[options.index(option)].winfo_children())


def _entry(parent: ctk.CTkFrame, hint: str, width: int) -> ctk.CTkEntry:
    entry = ctk.CTkEntry(
        parent, width=width, placeholder_text=hint, fg_color=theme.NIGHT, border_color=theme.RAISED
    )
    entry.pack(side="left", padx=(theme.UNIT, 0))
    return entry


def _button(parent: ctk.CTkFrame, text: str, command: Callable[[], None]) -> ctk.CTkButton:
    return ctk.CTkButton(
        parent, text=text, width=90, command=command, fg_color=theme.RAISED, hover_color=theme.HOVER
    )


def refill_now(cavebot: Any) -> str:
    """Send the running *cavebot* to town at its next ``check``; the message to show."""
    if cavebot is None:
        return "Start the bot with Cavebot on first"
    cavebot.refill_now = True
    return "Refill at the next check waypoint"


class CavebotTab(Tab):
    """Waypoints card beside one card switching Refill, Loot and Edit route."""

    title = "Cavebot"
    options = OPTIONS + LOOT_OPTIONS + REFILL_OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        root = ctk.CTkFrame(parent, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=10, pady=10)
        for col, weight in enumerate((3, 2)):
            root.grid_columnconfigure(col, weight=weight, uniform="cards")
        root.grid_rowconfigure(0, weight=1)
        self._root, self._locked = root, False

        box = section(root, "Waypoints")
        box.grid(row=0, column=0, sticky="nsew", padx=(0, theme.GAP))
        self.build_options(box, OPTIONS)
        self.menu_route = _menu(box, OPTIONS, ROUTE)
        self.menu_route.configure(command=self._pick_route)
        self._switches[SWITCH.field].configure(command=self._turn)
        for text, command in (("Delete", self._delete), ("Save", self._save), ("New", self._new)):
            button = _button(self.menu_route.master, text, command)
            button.configure(width=60)
            button.pack(side="right", padx=(theme.UNIT, 0))
            if text == "Save":
                self.btn_save = button
        self.lbl_pos = ctk.CTkLabel(
            box,
            text=position_line(Position(0, 0, 0)),
            anchor="w",
            font=ctk.CTkFont(family=theme.MONO, size=theme.SIZE_HEADING),
        )
        self.lbl_pos.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        self.lbl_hint = ctk.CTkLabel(
            box, text="", anchor="w", justify="left", wraplength=420, text_color=theme.TEXT_DIM
        )
        self.lbl_hint.pack(fill="x", padx=theme.PAD)
        self.wp_list = ctk.CTkTextbox(
            box, height=440, wrap="none", font=ctk.CTkFont(family=theme.MONO)
        )
        self.wp_list.pack(fill="both", expand=True, padx=theme.PAD, pady=(theme.UNIT, 0))
        self.wp_list.tag_config("current", foreground=theme.LIFE)
        self.wp_list.bind("<Double-Button-1>", self._pick_waypoint)
        self.lbl_current = ctk.CTkLabel(box, text="Not running", anchor="w")
        self.lbl_current.pack(fill="x", padx=theme.PAD)
        self.lbl_msg = ctk.CTkLabel(box, text="", anchor="w", text_color=theme.AMBER)
        self.lbl_msg.pack(fill="x", padx=theme.PAD, pady=(0, theme.UNIT))

        side = section(root, "Refill / Loot / Edit route")
        side.grid(row=0, column=1, sticky="nsew")
        pages = {
            name: ctk.CTkFrame(side, fg_color="transparent")
            for name in ("Refill", "Loot", "Edit route")
        }

        def show(name: str) -> None:
            for key, page in pages.items():
                page.pack_forget() if key != name else page.pack(fill="x")

        switch = ctk.CTkSegmentedButton(
            side,
            values=list(pages),
            command=show,
            selected_color=theme.HOVER,
            selected_hover_color=theme.HOVER,
        )
        switch.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        switch.set("Refill")
        refill = pages["Refill"]
        ctk.CTkLabel(
            refill,
            text=(
                "A 'check' waypoint with label 'town' jumps to label 'town' when your used"
                " capacity reaches the start % or Refill now was pressed. Put the town trip"
                " there and end it with a 'goto' back to the hunt."
            ),
            anchor="w",
            justify="left",
            wraplength=260,
            text_color=theme.TEXT_DIM,
        ).pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        self.build_options(refill, REFILL_OPTIONS)
        _button(refill, "Refill now", self._refill_now).pack(
            anchor="w", padx=theme.PAD, pady=(theme.UNIT, 0)
        )
        self.build_options(pages["Loot"], LOOT_OPTIONS)
        show("Refill")

        edit = pages["Edit route"]
        add = ctk.CTkFrame(edit, fg_color="transparent")
        add.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        self.ent_label = _entry(add, "label", 80)
        self.ent_x, self.ent_y, self.ent_z = (_entry(add, axis, 56) for axis in "XYZ")
        # doc: docs/prs/refill.md#drag-waypoint
        self.lbl_cursor = ctk.CTkLabel(
            edit, text="", anchor="w", font=ctk.CTkFont(family=theme.MONO)
        )
        self.lbl_cursor.pack(fill="x", padx=theme.PAD)
        self._capture: Any = None  # ScreenCapture, made on the first draw
        tools = ctk.CTkFrame(edit, fg_color="transparent")
        tools.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        for text, command in (("Undo", self._undo), ("Clear", self._clear)):
            _button(tools, text, command).pack(side="left", padx=(0, theme.UNIT))
        # T3.F9.K2: one button per waypoint type, at X, Y, Z or the bot's position.
        kinds = ctk.CTkFrame(edit, fg_color="transparent")
        kinds.pack(anchor="w", padx=theme.PAD, pady=(theme.PAD, 0))
        for row, group in enumerate(KIND_ROWS):
            for col, action in enumerate(group):
                button = _button(kinds, action.capitalize(), partial(self._add_action, action))
                button.configure(width=64)
                button.grid(row=row, column=col, padx=(0, theme.UNIT), pady=(0, theme.UNIT))
        # A stand waypoint one sqm away from X, Y, Z or the bot; the middle one is that sqm.
        pad = ctk.CTkFrame(edit, fg_color="transparent")
        pad.pack(anchor="w", padx=theme.PAD, pady=(theme.UNIT, 0))
        for i, (arrow, dx, dy) in enumerate(DIRECTIONS):
            button = _button(pad, arrow, partial(self._add_step, dx, dy))
            button.configure(width=32)
            button.grid(row=i // 3, column=i % 3, padx=1, pady=1)

        rec = ctk.CTkFrame(edit, fg_color="transparent")
        rec.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        self.btn_record = _button(rec, "Start recording", self._toggle)
        self.btn_record.configure(width=130)
        self.btn_record.pack(side="left")
        ctk.CTkLabel(rec, text="walk every").pack(side="left", padx=(theme.UNIT, 0))
        self.ent_tiles = _entry(rec, "", 36)
        self.ent_tiles.insert(0, "5")
        ctk.CTkLabel(rec, text="tiles").pack(side="left", padx=theme.UNIT)

        self._pos = Position(0, 0, 0)
        #: The last position read, shown while the reader is lost.
        self._last = Position(0, 0, 0)
        self._status = ""
        self._routes_dir = Path()
        #: The route the editor saves to ("" until New or a pick).
        self._name = ""
        self._waypoints: list[Waypoint] = []
        #: The editor has edits its route file does not.
        self._dirty = False
        self._error = ""
        self._text = ""
        self._recording = False
        self._stand = StandTracker()
        self._stood_before: Position | None = None  # for the stairs walked over
        # The cavebot runs this list, saved or not.
        loop_factory.editor_route = lambda: (self._name, self._waypoints)
        self._reload()

    def _refresh_names(self) -> list[str]:
        """Fill the route dropdowns from disk; a new route with no waypoints is listed too."""
        names = route_names(self._routes_dir)
        listed = names if not self._name or self._name in names else [*names, self._name]
        self.menu_route.configure(values=listed or [NO_ROUTES])
        return names

    def _reload(self) -> None:
        cfg = load_real_config()
        self._routes_dir = Path(cfg.routes_dir)
        names = self._refresh_names()
        picked = picked_route(cfg)
        self.menu_route.set(picked if picked in names else (PICK if names else NO_ROUTES))
        start = read_option(cfg, START.field)
        self._writers[START.field](start + 1 if isinstance(start, int) else 1)
        self._show_route(self.menu_route.get())

    def _discard_ok(self) -> bool:
        """True when there are no unsaved edits, or the user lets them go."""
        return not self._dirty or messagebox.askyesno("Unsaved changes", "Discard unsaved changes?")

    def _pick_route(self, name: str) -> None:
        """Load route *name* into the editor and make it the cavebot's route."""
        if name == self._name:
            return
        if not self._discard_ok():
            self.menu_route.set(self._name or PICK)
            return
        self._show_route(name)
        self.submit()

    def _turn(self) -> None:
        """Save the switch; flip it back with the reason when it is refused."""
        try:
            self.apply()
        except ConfigError as exc:
            self._writers[SWITCH.field](False)
            self.lbl_hint.configure(text=plain_reason(str(exc)))
            return
        self.lbl_hint.configure(text="")
        self.ctx.config_changed()

    def _show_route(self, name: str) -> None:
        """Load route *name* into the editor; unsaved edits are dropped."""
        self._waypoints = []
        self._error = ""
        self._name = ""
        if name not in (NO_ROUTES, PICK):
            self._name = name
            try:
                route = RouteFileLoader.load(self._routes_dir / f"{name}.json")
                self._waypoints = list(route.waypoints)
            except RouteError as exc:
                self._error = str(exc)
        self._set_dirty(False)

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self.btn_save.configure(text="Save*" if dirty else "Save")
        self._draw()

    def _lock(self, locked: bool) -> None:
        """Gray out every control while Cavebot is on; the switch and card pickers stay."""
        self._locked = locked
        state = "disabled" if locked else "normal"
        stack = list(self._root.winfo_children())
        while stack:
            widget = stack.pop()
            if widget is self._switches[SWITCH.field]:
                continue
            if isinstance(widget, CONTROLS):
                widget.configure(state=state)
            elif not isinstance(widget, ctk.CTkSegmentedButton):
                stack.extend(widget.winfo_children())

    def _draw(self) -> None:
        live = self.ctx.live_route()
        on = bool(self._switches[SWITCH.field].get())
        if on != self._locked:
            self._lock(on)
        current = live.current if live is not None and live.name == self._name else None
        route = Route("editor", waypoints=self._waypoints)
        text = self._error or waypoint_table(route, current)
        running = "Not running"
        if live:
            running = f"Current: {waypoint_line(live.current, len(live.waypoints))}"
        lost = self._status == "lost"
        hint = LOST_HINT if lost else ("" if self._waypoints or self._name else NO_ROUTES_HINT)
        for label, value in (
            (self.lbl_current, running),
            (self.lbl_pos, position_line(self._last if lost else self._pos, self._status)),
            (self.lbl_hint, hint),
            (self.lbl_cursor, self._cursor_text()),
        ):
            if label.cget("text") != value:
                label.configure(text=value)
        if text == self._text:
            return
        self._text = text
        self.wp_list.configure(state="normal")
        self.wp_list.delete("1.0", "end")
        self.wp_list.insert("1.0", text)
        if current is not None and not self._error:  # line 1 is the header
            self.wp_list.tag_add("current", f"{current + 2}.0", f"{current + 2}.end")
            self.wp_list.see(f"{current + 2}.0")
        self.wp_list.configure(state="disabled")

    def _refill_now(self) -> None:
        self.lbl_msg.configure(text=refill_now(self.ctx.live_cavebot()))

    def _cursor_text(self) -> str:
        from moonlight.perception.capture import ScreenCapture
        from moonlight.perception.cursor import screen_cursor

        if self._capture is None:
            self._capture = ScreenCapture(window_title=load_real_config().window_title)
        return cursor_line(screen_cursor(), self._capture.get_window_rect())

    def _pick_waypoint(self, event: tk.Event) -> str:
        """Double-click: the running bot walks to that waypoint next, else starts there."""
        line = int(str(self.wp_list.index(f"@{event.x},{event.y}")).split(".")[0])
        i = None if self._error else picked_waypoint(line, len(self._waypoints))
        if i is None:
            return "break"
        self._writers[START.field](i + 1)
        cavebot = self.ctx.live_cavebot()
        if cavebot is not None and cavebot.route.name == self._name:
            cavebot.go_to(i)
            self._text = ""  # redraw the highlight
            self._draw()
            self.lbl_msg.configure(text=f"Walking to waypoint {i + 1}")
        elif not self.ctx.bot_running():
            self.submit()
            self.lbl_msg.configure(text=f"The bot starts at waypoint {i + 1}")
        else:
            self.lbl_msg.configure(text="The bot runs another route; stop it to start this one")
        return "break"

    def _changed(self) -> None:
        """Mark the editor unsaved and redraw; Save writes the route file."""
        self._error = ""
        self._set_dirty(True)

    def _save(self) -> None:
        """Write the editor to its route file; a new route becomes the cavebot's route."""
        if not self._name:
            self.lbl_msg.configure(text="Not saved: press New to name the route")
            return
        if not self._waypoints:
            self.lbl_msg.configure(text="Not saved: add a waypoint first")
            return
        first = self._name not in route_names(self._routes_dir)
        try:
            save_route(self._routes_dir, self._name, self._waypoints)
        except (OSError, RouteError) as exc:
            self.lbl_msg.configure(text=f"Not saved: {exc}")
            return
        self._set_dirty(False)
        self.lbl_msg.configure(text=f"Saved {self._name}")
        if first:
            self._refresh_names()
            self.menu_route.set(self._name)
            self.submit()

    def _add_action(self, action: str) -> None:
        """Add *action* at the character's tile; X, Y, Z when typed and not recording."""
        typed = (self.ent_x.get(), self.ent_y.get(), self.ent_z.get())
        if self._recording:
            typed = ("",) * 3
        at = self._stand.stood if self._recording else None
        try:
            wp = make_waypoint(action, self.ent_label.get(), typed, at or self._pos)
        except RouteError as exc:
            self.lbl_msg.configure(text=str(exc))
            return
        self._waypoints.append(wp)
        self.lbl_msg.configure(text=f"Added {wp.action} ({len(self._waypoints)} waypoints)")
        self._changed()

    def _add_step(self, dx: int, dy: int) -> None:
        """Add a stand waypoint *dx*, *dy* sqm from X, Y, Z or the bot."""
        manual = (self.ent_x.get(), self.ent_y.get(), self.ent_z.get())
        try:
            wp = make_waypoint("stand", "", manual, self._pos)
        except RouteError as exc:
            self.lbl_msg.configure(text=str(exc))
            return
        self._waypoints.append(replace(wp, x=wp.x + dx, y=wp.y + dy))
        self.lbl_msg.configure(text=f"Added stand ({len(self._waypoints)} waypoints)")
        self._changed()

    def _undo(self) -> None:
        if self._waypoints:
            self._waypoints.pop()
            self._changed()

    def _clear(self) -> None:
        """Empty the editor; the route file keeps its waypoints until Save."""
        if self._waypoints:
            self._waypoints.clear()
            self._changed()

    def _new(self) -> None:
        """Ask for a name and start that route; unnamed editor waypoints go into it."""
        if not self._discard_ok():
            return
        name = (ctk.CTkInputDialog(text="Route name", title="New route").get_input() or "").strip()
        if not name:
            return
        try:
            exists = route_path(self._routes_dir, name).exists()
        except RouteError as exc:
            self.lbl_msg.configure(text=str(exc))
            return
        if exists:
            self.lbl_msg.configure(text=f"Route {name} already exists. Pick it instead.")
            return
        if self._name:
            self._waypoints = []
        self._name = name
        self._refresh_names()
        self.menu_route.set(name)
        self._changed()
        self.lbl_msg.configure(text=f"New route {name}. Press Save to keep it.")

    def _delete(self) -> None:
        """Delete the dropdown's route, after confirming; the cavebot moves off it first."""
        name = self.menu_route.get()
        if name not in route_names(self._routes_dir):
            self.lbl_msg.configure(text="Pick a route to delete")
            return
        if not messagebox.askyesno("Delete route", f"Delete route {name}?"):
            return
        path = picked_config()
        try:
            drop_route(load_real_config(path), name).save(path)
        except ConfigError as exc:
            self.lbl_msg.configure(text=f"Not deleted: {plain_reason(str(exc))}")
            return
        route_path(self._routes_dir, name).unlink()
        self.ctx.config_changed()
        self._reload()
        self.lbl_msg.configure(text=f"Deleted {name}")

    def _toggle(self) -> None:
        if self._recording:
            self._stop_recording()
            return
        self._recording = True
        self._stand = StandTracker()
        self._stood_before = None
        self.btn_record.configure(text="Stop recording")
        self._draw()

    def _stop_recording(self) -> None:
        self._recording = False
        self.btn_record.configure(text="Start recording")
        self._draw()

    def _auto_stop_recording(self) -> None:
        """The cavebot started: stop recording and save what was recorded."""
        self._stop_recording()
        if self._dirty:
            self._save()
        saved = self.lbl_msg.cget("text")
        self.lbl_msg.configure(text=f"Cavebot started, recording stopped. {saved}")

    def on_poll(self, snap: WorldState) -> None:
        self.show_paused([SWITCH.field], self.ctx.paused)
        self._pos = pos = snap.position
        self._status = snap.position_status
        if pos != Position(0, 0, 0):
            self._last = pos
        cavebot_on = bool(self._switches[SWITCH.field].get()) or self.ctx.live_cavebot() is not None
        if should_stop_recording(self._recording, cavebot_on):
            self._auto_stop_recording()
        if self._recording:
            self._stand.update(pos)
            # The tile stood on, never the one being walked to.
            stood = self._stand.stood
            try:
                tiles = max(1, int(self.ent_tiles.get()))
            except ValueError:
                tiles = 5
            if stood is not None and self._stood_before is not None:
                stairs = stairs_waypoint(self._waypoints, self._stood_before, stood)
                if stairs is not None:
                    self._waypoints.append(stairs)
            self._stood_before = stood or self._stood_before
            if stood is not None and should_add(self._waypoints, stood, tiles):
                self._waypoints.append(Waypoint(stood.x, stood.y, stood.z))
                self._changed()
        self._draw()

    def reload_options(self) -> None:
        super().reload_options()
        self._reload()

    def apply(self) -> BotConfig:
        path = picked_config()
        values = {field: read() for field, read in self._readers.items()}
        cfg = apply_cavebot(load_real_config(path), values)
        cfg.save(path)
        return cfg
