"""Cavebot tab: route picker, tool hotkeys and the route's waypoints (T3.F1.K1).

Routes are the bot's own files, ``<routes_dir>/<name>.json``, read by
:class:`RouteFileLoader`. ``cavebot.route`` is saved by name, so the route
dropdown holds names.
"""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from moonlight.bot.state import WorldState
from moonlight.decision.cavebot import Route, RouteError, RouteFileLoader
from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config, picked_config
from moonlight.gui.tabs.base import Option, Tab, read_option, section, write_option
from moonlight.setup.config import BotConfig, ConfigError

__all__ = [
    "OPTIONS",
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
    Option("Cavebot", "cavebot.enabled", "switch"),
    ROUTE,
    START,
    Option("Rope hotkey", "cavebot.rope_hotkey", "entry"),
    Option("Shovel hotkey", "cavebot.shovel_hotkey", "entry"),
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


def apply_cavebot(cfg: BotConfig, values: dict[str, object]) -> BotConfig:
    """*cfg* with the cavebot option *values*, the route written first.

    With no route on disk picked nothing changes, so Save still works for
    an off cavebot without routes. A blank hotkey keeps its default.
    The start waypoint is given 1-based.

    Raises:
        ConfigError: For a refused value.
    """
    if values.get(ROUTE.field) not in route_names(cfg.routes_dir):
        return cfg
    start = values.get(START.field, "")
    if start != "":
        try:
            values = {**values, START.field: int(str(start)) - 1}
        except ValueError:
            raise ConfigError(f"start waypoint {start!r} is not a whole number") from None
    for option in sorted(OPTIONS, key=lambda o: o is not ROUTE):
        if values.get(option.field, "") != "":
            cfg = write_option(cfg, option, values[option.field])
    return cfg


class CavebotTab(Tab):
    """Route, tool hotkeys and the picked route's waypoints."""

    title = "Cavebot"
    options = OPTIONS

    def build(self, parent: ctk.CTkFrame) -> None:
        box = section(parent, "Cavebot")
        box.pack(fill="x", padx=10, pady=10)
        self.build_options(box)
        rows = [w for w in box.winfo_children() if isinstance(w, ctk.CTkFrame)]
        row = rows[OPTIONS.index(ROUTE)]
        self.menu_route = next(
            w for w in row.winfo_children() if isinstance(w, ctk.CTkOptionMenu)
        )
        self.menu_route.configure(command=self._show_route)
        ctk.CTkButton(
            row,
            text="Reload",
            width=70,
            command=self._reload,
            fg_color=theme.RAISED,
            hover_color=theme.HOVER,
        ).pack(side="right", padx=theme.UNIT)
        ctk.CTkFrame(box, height=theme.UNIT, fg_color="transparent").pack()

        wps = section(parent, "Waypoints")
        wps.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.wp_list = ctk.CTkTextbox(
            wps, height=200, wrap="none", font=ctk.CTkFont(family=theme.MONO)
        )
        self.wp_list.pack(fill="both", expand=True, padx=theme.PAD, pady=(theme.PAD, 0))
        self.lbl_current = ctk.CTkLabel(wps, text="Not running", anchor="w")
        self.lbl_current.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))
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

    def on_poll(self, snap: WorldState) -> None:
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
