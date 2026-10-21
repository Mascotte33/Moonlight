"""Cavebot tab: Route, Refill and Loot sub-tabs (T3.F1.K1, T3.F7.K1, T3.F8.K1).

Routes are the bot's own files, ``<routes_dir>/<name>.json``, read by
:class:`RouteFileLoader`. ``cavebot.route`` and ``refill.route_name`` are
saved by name, so their dropdowns hold names.
"""

from __future__ import annotations

import json
from pathlib import Path

import customtkinter as ctk

from moonlight.bot.state import Position, WorldState
from moonlight.decision.cavebot import Route, RouteError, RouteFileLoader
from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config, picked_config
from moonlight.gui.tabs.base import Option, Tab, read_option, section, write_option
from moonlight.setup.config import BotConfig, ConfigError

__all__ = [
    "LOOT_OPTIONS",
    "OPTIONS",
    "REFILL_OPTIONS",
    "ROUTE",
    "CavebotTab",
    "add_waypoint",
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
    Option("Start refill at cap (oz)", "refill.trigger_cap", "entry"),
    Option("Stop at cap (oz)", "refill.stop_cap", "entry"),
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
    """*route*'s waypoints as a text table, row *current* (0-based) marked ``>``."""
    if route is None or not route.waypoints:
        return "No waypoints loaded"
    lines = [f" {'#':>3}  {'action':<7}{'x':>6}{'y':>6}{'z':>4}  label"]
    for i, wp in enumerate(route.waypoints):
        mark = ">" if i == current else " "
        lines.append(
            f"{mark}{i + 1:>3}  {wp.action:<7}{wp.x:>6}{wp.y:>6}{wp.z:>4}  {wp.label}"
        )
    return "\n".join(lines)


def waypoint_line(current: int | None, total: int) -> str:
    """"Waypoint N of M" for 0-based *current*, or "Not running" for None."""
    return "Not running" if current is None else f"Waypoint {current + 1} of {total}"


def add_waypoint(path: Path, pos: Position) -> str:
    """Append *pos* as a ``walk`` waypoint to route file *path*; the result text.

    Position ``(0, 0, 0)`` is unknown and adds nothing.
    """
    if (pos.x, pos.y, pos.z) == (0, 0, 0):
        return "Position unknown"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["waypoints"].append({"x": pos.x, "y": pos.y, "z": pos.z, "action": "walk"})
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return f"Added {pos.x}, {pos.y}, {pos.z}"


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
        ctk.CTkFrame(box, height=theme.UNIT, fg_color="transparent").pack()

        wps = section(page, "Waypoints")
        wps.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.wp_list = ctk.CTkTextbox(
            wps, height=200, wrap="none", font=ctk.CTkFont(family=theme.MONO)
        )
        self.wp_list.pack(fill="both", expand=True, padx=theme.PAD, pady=(theme.PAD, 0))
        self.lbl_current = ctk.CTkLabel(wps, text="Not running", anchor="w")
        self.lbl_current.pack(fill="x", padx=theme.PAD)
        add = ctk.CTkFrame(wps, fg_color="transparent")
        add.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))
        ctk.CTkButton(
            add,
            text="Add current position",
            command=self._add_position,
            fg_color=theme.RAISED,
            hover_color=theme.HOVER,
        ).pack(side="left")
        self.lbl_added = ctk.CTkLabel(add, text="", anchor="w", text_color=theme.TEXT_MUTED)
        self.lbl_added.pack(side="left", padx=theme.UNIT)
        self._pos = Position(0, 0, 0)
        self._routes_dir = Path()
        self._route: Route | None = None
        self._error = ""
        self._text = ""
        self._reload()

    def _reload(self) -> None:
        cfg = load_real_config()
        self._routes_dir = Path(cfg.routes_dir)
        names = route_names(self._routes_dir)
        self.menu_route.configure(values=names or [NO_ROUTES])
        self.menu_refill_route.configure(values=names or [NO_ROUTES])
        picked = picked_route(cfg)
        self.menu_route.set(picked if picked in names else (names[0] if names else NO_ROUTES))
        start = read_option(cfg, START.field)
        self._writers[START.field](start + 1 if isinstance(start, int) else 1)
        self._show_route(self.menu_route.get())

    def _show_route(self, name: str) -> None:
        self._route = None
        self._error = ""
        if name != NO_ROUTES:
            try:
                self._route = RouteFileLoader.load(self._routes_dir / f"{name}.json")
            except RouteError as exc:
                self._error = str(exc)
        self._draw()

    def _draw(self) -> None:
        live = self.ctx.live_route()
        route = live if live is not None else self._route
        current = live.current if live is not None else None
        text = self._error if live is None and self._error else waypoint_table(route, current)
        line = waypoint_line(current, len(route.waypoints) if route else 0)
        if self.lbl_current.cget("text") != line:
            self.lbl_current.configure(text=line)
        if text == self._text:
            return
        self._text = text
        self.wp_list.configure(state="normal")
        self.wp_list.delete("1.0", "end")
        self.wp_list.insert("1.0", text)
        self.wp_list.configure(state="disabled")

    def _add_position(self) -> None:
        name = self.menu_route.get()
        if name == NO_ROUTES:
            self.lbl_added.configure(text="No route picked")
            return
        self.lbl_added.configure(text=add_waypoint(self._routes_dir / f"{name}.json", self._pos))
        self._show_route(name)

    def on_poll(self, snap: WorldState) -> None:
        self._pos = snap.position
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
