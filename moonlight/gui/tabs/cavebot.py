"""Cavebot tab: route picker, tool hotkeys and the route's waypoints (T3.F1.K1).

Routes are the bot's own files, ``<routes_dir>/<name>.json``, read by
:class:`RouteFileLoader`. ``cavebot.route`` is saved by name, so the route
dropdown holds names.
"""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from moonlight.decision.cavebot import Route, RouteError, RouteFileLoader
from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config, picked_config
from moonlight.gui.tabs.base import Option, Tab, read_option, section, write_option
from moonlight.setup.config import BotConfig

__all__ = [
    "OPTIONS",
    "ROUTE",
    "CavebotTab",
    "apply_cavebot",
    "picked_route",
    "route_names",
    "waypoint_table",
]

NO_ROUTES = "No routes"
ROUTE = Option("Route", "cavebot.route", "dropdown")
OPTIONS = [
    Option("Cavebot", "cavebot.enabled", "switch"),
    ROUTE,
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


def waypoint_table(route: Route | None) -> str:
    """*route*'s waypoints as a text table, or "No waypoints loaded"."""
    if route is None or not route.waypoints:
        return "No waypoints loaded"
    lines = [f"{'#':>3}  {'action':<7}{'x':>6}{'y':>6}{'z':>4}  label"]
    for i, wp in enumerate(route.waypoints, 1):
        lines.append(f"{i:>3}  {wp.action:<7}{wp.x:>6}{wp.y:>6}{wp.z:>4}  {wp.label}")
    return "\n".join(lines)


def apply_cavebot(cfg: BotConfig, values: dict[str, object]) -> BotConfig:
    """*cfg* with the cavebot option *values*, the route written first.

    With no route on disk picked nothing changes, so Save still works for
    an off cavebot without routes. A blank hotkey keeps its default.

    Raises:
        ConfigError: For a refused value.
    """
    if values.get(ROUTE.field) not in route_names(cfg.routes_dir):
        return cfg
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
        self.wp_list.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        self._routes_dir = Path()
        self._reload()

    def _reload(self) -> None:
        cfg = load_real_config()
        self._routes_dir = Path(cfg.routes_dir)
        names = route_names(self._routes_dir)
        self.menu_route.configure(values=names or [NO_ROUTES])
        picked = picked_route(cfg)
        self.menu_route.set(picked if picked in names else (names[0] if names else NO_ROUTES))
        self._show_route(self.menu_route.get())

    def _show_route(self, name: str) -> None:
        text = waypoint_table(None)
        if name != NO_ROUTES:
            try:
                text = waypoint_table(RouteFileLoader.load(self._routes_dir / f"{name}.json"))
            except RouteError as exc:
                text = str(exc)
        self.wp_list.configure(state="normal")
        self.wp_list.delete("1.0", "end")
        self.wp_list.insert("1.0", text)
        self.wp_list.configure(state="disabled")

    def reload_options(self) -> None:
        super().reload_options()
        self._reload()

    def apply(self) -> BotConfig:
        path = picked_config()
        values = {field: read() for field, read in self._readers.items()}
        cfg = apply_cavebot(load_real_config(path), values)
        cfg.save(path)
        return cfg
