"""Cavebot tab: Route, Refill and Loot sub-tabs (T3.F1.K1, T3.F7.K1, T3.F8.K1).

Routes are the bot's own files, ``<routes_dir>/<name>.json``, read by
:class:`RouteFileLoader`. ``cavebot.route`` and ``refill.route_name`` are
saved by name, so their dropdowns hold names. The Route sub-tab is also the
route editor: the picked route loads into it, and waypoints are added by
hand or recorded while walking (T3.F9.K1).
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from functools import partial
from pathlib import Path

import customtkinter as ctk

from moonlight.bot.state import Position, WorldState
from moonlight.decision.cavebot import ACTIONS, Route, RouteError, RouteFileLoader, Waypoint
from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config, picked_config
from moonlight.gui.tabs.base import Option, Tab, read_option, section, write_option
from moonlight.gui.tabs.recorder import (
    HOTKEYS,
    Hotkeys,
    make_waypoint,
    save_route,
    should_add,
    status_line,
)
from moonlight.setup.config import BotConfig, ConfigError

__all__ = [
    "LOOT_OPTIONS",
    "OPTIONS",
    "REFILL_OPTIONS",
    "ROUTE",
    "CavebotTab",
    "apply_cavebot",
    "picked_route",
    "route_names",
    "waypoint_line",
    "waypoint_table",
]

NO_ROUTES = "No routes"
ROUTE = Option("Route", "cavebot.route", "dropdown")
#: Shown 1-based; ``cavebot.start_index`` is 0-based.
START = Option("Start from waypoint", "cavebot.start_index", "entry")
OPTIONS = [
    ROUTE,
    START,
    Option("Rope hotkey", "cavebot.rope_hotkey", "entry"),
    Option("Shovel hotkey", "cavebot.shovel_hotkey", "entry"),
]
LOOT_OPTIONS = [
    Option("Loot hotkey", "looting.loot_hotkey", "entry"),
    Option("Move-all hotkey (blank = none)", "looting.move_all_hotkey", "entry"),
    Option("Min cap to loot (oz)", "looting.min_cap_to_loot", "entry"),
]
REFILL_ROUTE = Option("Refill route", "refill.route_name", "dropdown")
REFILL_OPTIONS = [
    Option("Start refill at used cap %", "refill.trigger_cap", "entry"),
    Option("Stop at used cap %", "refill.stop_cap", "entry"),
    REFILL_ROUTE,
    Option("Storage hotkey", "refill.storage_hotkey", "entry"),
    Option("Deposit hotkey (blank = none)", "refill.deposit_hotkey", "entry"),
]


def route_names(routes_dir: Path) -> list[str]:
    """The route names in *routes_dir* (its ``*.json`` file names), sorted."""
    return sorted(p.stem for p in Path(routes_dir).glob("*.json"))


def picked_route(cfg: BotConfig) -> str:
    """The name of *cfg*'s cavebot route, or "" when it has none."""
    route = read_option(cfg, ROUTE.field)
    return route.name if isinstance(route, Route) else ""


def waypoint_table(route: Route | None, current: int | None = None) -> str:
    """*route*'s waypoints as a text table, row *current* (0-based) marked ``>``.

    The first waypoint is where the bot starts, so its row says START.
    """
    if route is None or not route.waypoints:
        return "No waypoints loaded"
    lines = [f" {'#':>3}  {'action':<7}{'x':>6}{'y':>6}{'z':>4}  label"]
    for i, wp in enumerate(route.waypoints):
        mark = ">" if i == current else " "
        start = "  START" if i == 0 else ""
        lines.append(
            f"{mark}{i + 1:>3}  {wp.action:<7}{wp.x:>6}{wp.y:>6}{wp.z:>4}  {wp.label}{start}"
        )
    return "\n".join(lines)


def waypoint_line(current: int | None, total: int) -> str:
    """"Waypoint N of M" for 0-based *current*, or "Not running" for None."""
    return "Not running" if current is None else f"Waypoint {current + 1} of {total}"


def apply_cavebot(cfg: BotConfig, values: dict[str, object]) -> BotConfig:
    """*cfg* with the cavebot, loot and refill option *values*.

    The cavebot route is written first. With no route on disk picked the
    cavebot options are skipped, so Save still works for an off cavebot
    without routes. A blank cavebot hotkey keeps its default; a blank
    deposit or move-all hotkey means none. The start waypoint is given
    1-based. Loot goes before refill, which needs looting on.

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
            if values.get(option.field, "") != "":
                cfg = write_option(cfg, option, values[option.field])
    for option in LOOT_OPTIONS + REFILL_OPTIONS:
        if option.field in values:
            cfg = write_option(cfg, option, values[option.field])
    return cfg


def _menu(box: ctk.CTkFrame, options: list[Option], option: Option) -> ctk.CTkOptionMenu:
    """The dropdown :meth:`Tab.build_options` made for *option* in *box*."""
    rows = [w for w in box.winfo_children() if isinstance(w, ctk.CTkFrame)]
    return next(
        w for w in rows[options.index(option)].winfo_children()
        if isinstance(w, ctk.CTkOptionMenu)
    )


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


class CavebotTab(Tab):
    """Route sub-tab (route, hotkeys, waypoints), Refill and Loot sub-tabs."""

    title = "Cavebot"
    options = OPTIONS + LOOT_OPTIONS + REFILL_OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        tabs = ctk.CTkTabview(
            parent,
            fg_color=theme.NIGHT,
            segmented_button_selected_color=theme.HOVER,
            segmented_button_selected_hover_color=theme.HOVER,
        )
        tabs.pack(fill="both", expand=True)
        page = tabs.add("Route")
        refill_page = tabs.add("Refill")
        loot = section(tabs.add("Loot"), "Loot")
        loot.pack(fill="x", padx=10, pady=10)
        self.build_options(loot, LOOT_OPTIONS)
        ctk.CTkFrame(loot, height=theme.UNIT, fg_color="transparent").pack()

        refill = section(refill_page, "Refill")
        refill.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(
            refill,
            text=(
                "When your used capacity reaches the start %, the bot walks the refill route"
                " to its 'storage' label. It deposits until used capacity is down to the stop %,"
                " then walks to the 'farming' label and hunts again."
            ),
            anchor="w",
            justify="left",
            wraplength=640,
            text_color=theme.TEXT_DIM,
        ).pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        self.build_options(refill, REFILL_OPTIONS)
        self.menu_refill_route = _menu(refill, REFILL_OPTIONS, REFILL_ROUTE)
        ctk.CTkFrame(refill, height=theme.UNIT, fg_color="transparent").pack()

        box = section(page, "Cavebot")
        box.pack(fill="x", padx=10, pady=10)
        self.build_options(box, OPTIONS)
        self.menu_route = _menu(box, OPTIONS, ROUTE)
        self.menu_route.configure(command=self._show_route)
        ctk.CTkButton(
            self.menu_route.master,
            text="Reload",
            width=70,
            command=self._reload,
            fg_color=theme.RAISED,
            hover_color=theme.HOVER,
        ).pack(side="right", padx=theme.UNIT)
        self.lbl_hint = ctk.CTkLabel(box, text="", anchor="w", text_color=theme.TEXT_DIM)
        self.lbl_hint.pack(fill="x", padx=theme.PAD, pady=(0, theme.UNIT))

        wps = section(page, "Route editor")
        wps.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        status = ctk.CTkFrame(wps, fg_color="transparent")
        status.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        self.lbl_current = ctk.CTkLabel(status, text="Not running", anchor="w")
        self.lbl_current.pack(side="left")
        self.lbl_pos = ctk.CTkLabel(
            status, text=status_line(False, Position(0, 0, 0)), text_color=theme.TEXT_DIM
        )
        self.lbl_pos.pack(side="right")
        self.lbl_msg = ctk.CTkLabel(wps, text="", anchor="w", text_color=theme.AMBER)
        self.lbl_msg.pack(fill="x", padx=theme.PAD)

        add = ctk.CTkFrame(wps, fg_color="transparent")
        add.pack(fill="x", padx=theme.PAD)
        self.ent_label = _entry(add, "label", 90)
        self.ent_x, self.ent_y, self.ent_z = (_entry(add, axis, 60) for axis in "XYZ")
        for text, command in (("Clear", self._clear), ("Undo", self._undo)):
            _button(add, text, command).pack(side="right", padx=(theme.UNIT, 0))
        # T3.F9.K2: one button per waypoint type, at X, Y, Z or the bot's position.
        kinds = ctk.CTkFrame(wps, fg_color="transparent")
        kinds.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        for action in ACTIONS:
            button = _button(kinds, action.capitalize(), partial(self._add_action, action))
            button.configure(width=70)
            button.pack(side="left", padx=(0, theme.UNIT))

        rec = ctk.CTkFrame(wps, fg_color="transparent")
        rec.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        self.btn_record = _button(rec, "Start recording", self._toggle)
        self.btn_record.configure(width=130)
        self.btn_record.pack(side="left")
        ctk.CTkLabel(rec, text="add walk every").pack(side="left", padx=(theme.PAD, 0))
        self.ent_tiles = _entry(rec, "", 40)
        self.ent_tiles.insert(0, "5")
        ctk.CTkLabel(rec, text="tiles").pack(side="left", padx=theme.UNIT)
        keys = "  ".join(f"{k} {a}" for k, a in sorted(HOTKEYS.items()))
        ctk.CTkLabel(
            rec, text=keys, text_color=theme.TEXT_MUTED, font=ctk.CTkFont(family=theme.MONO)
        ).pack(side="right")

        save = ctk.CTkFrame(wps, fg_color="transparent")
        save.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        _button(save, "Save route", self._save).pack(side="right", padx=(theme.UNIT, 0))
        self.ent_name = _entry(save, "route name, e.g. rotworms", 140)
        self.ent_name.pack_configure(fill="x", expand=True, padx=0)

        self.wp_list = ctk.CTkTextbox(
            wps, height=160, wrap="none", font=ctk.CTkFont(family=theme.MONO)
        )
        self.wp_list.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        self._pos = Position(0, 0, 0)
        self._routes_dir = Path()
        self._waypoints: list[Waypoint] = []
        self._error = ""
        self._text = ""
        self._recording = False
        self._pressed: queue.Queue[str] = queue.Queue()
        self._hotkeys = Hotkeys(self._pressed)
        self.btn_record.bind("<Destroy>", lambda _e: self._hotkeys.stop(), add="+")
        self._reload()

    def _refresh_names(self) -> list[str]:
        names = route_names(self._routes_dir)
        self.menu_route.configure(values=names or [NO_ROUTES])
        self.menu_refill_route.configure(values=names or [NO_ROUTES])
        hint = "No routes yet. Press a waypoint button below, name the route, then Save route."
        self.lbl_hint.configure(text="" if names else hint)
        return names

    def _reload(self) -> None:
        cfg = load_real_config()
        self._routes_dir = Path(cfg.routes_dir)
        names = self._refresh_names()
        picked = picked_route(cfg)
        self.menu_route.set(picked if picked in names else (names[0] if names else NO_ROUTES))
        start = read_option(cfg, START.field)
        self._writers[START.field](start + 1 if isinstance(start, int) else 1)
        self._show_route(self.menu_route.get())

    def _show_route(self, name: str) -> None:
        """Load route *name* into the editor."""
        self._waypoints = []
        self._error = ""
        if name != NO_ROUTES:
            try:
                route = RouteFileLoader.load(self._routes_dir / f"{name}.json")
                self._waypoints = list(route.waypoints)
            except RouteError as exc:
                self._error = str(exc)
            self.ent_name.delete(0, "end")
            self.ent_name.insert(0, name)
        self._draw()

    def _draw(self) -> None:
        live = self.ctx.live_route()
        route = live if live is not None else Route("editor", waypoints=self._waypoints)
        current = live.current if live is not None else None
        text = self._error if live is None and self._error else waypoint_table(route, current)
        for label, value in (
            (self.lbl_current, waypoint_line(current, len(route.waypoints))),
            (self.lbl_pos, status_line(self._recording, self._pos)),
        ):
            if label.cget("text") != value:
                label.configure(text=value)
        if text == self._text:
            return
        self._text = text
        self.wp_list.configure(state="normal")
        self.wp_list.delete("1.0", "end")
        self.wp_list.insert("1.0", text)
        self.wp_list.configure(state="disabled")

    def _add_action(self, action: str) -> None:
        manual = (self.ent_x.get(), self.ent_y.get(), self.ent_z.get())
        try:
            wp = make_waypoint(action, self.ent_label.get(), manual, self._pos)
        except RouteError as exc:
            self.lbl_msg.configure(text=str(exc))
            return
        self._waypoints.append(wp)
        self._error = ""
        self.lbl_msg.configure(text=f"Added {wp.action} ({len(self._waypoints)} waypoints)")
        self._draw()

    def _undo(self) -> None:
        if self._waypoints:
            self._waypoints.pop()
            self._draw()

    def _clear(self) -> None:
        self._waypoints.clear()
        self._draw()

    def _save(self) -> None:
        name = self.ent_name.get().strip()
        try:
            path = save_route(self._routes_dir, name, self._waypoints)
        except (OSError, RouteError) as exc:
            self.lbl_msg.configure(text=f"Save failed: {exc}")
            return
        self._refresh_names()
        self.menu_route.set(name)
        self.lbl_msg.configure(text=f"Saved {path}")

    def _toggle(self) -> None:
        self._recording = not self._recording
        self.btn_record.configure(text="Stop recording" if self._recording else "Start recording")
        if self._recording:
            self._hotkeys.start()
            if self._hotkeys.failed:
                self.lbl_msg.configure(
                    text=f"Hotkeys in use elsewhere: {', '.join(self._hotkeys.failed)}"
                )
        else:
            self._hotkeys.stop()
        self._draw()

    def on_poll(self, snap: WorldState) -> None:
        self._pos = pos = snap.position
        if self._recording:
            while not self._pressed.empty():
                action = self._pressed.get_nowait()
                if action == "undo":
                    self._undo()
                else:
                    self._add_action(action)
            try:
                tiles = max(1, int(self.ent_tiles.get()))
            except ValueError:
                tiles = 5
            if should_add(self._waypoints, pos, tiles):
                self._waypoints.append(Waypoint(pos.x, pos.y, pos.z))
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
