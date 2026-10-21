"""Recorder tab: capture waypoints and save them as bot routes (T8.F1.K1).

Routes are the bot's own files, ``<routes_dir>/<name>.json``, in the format
:class:`RouteFileLoader` reads, so the Cavebot tab can pick and run them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import customtkinter as ctk

from moonlight.bot.state import Position, WorldState
from moonlight.decision.cavebot import ACTIONS, Route, RouteError, RouteFileLoader, Waypoint
from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config
from moonlight.gui.tabs.base import Tab, section, stat_label
from moonlight.gui.tabs.cavebot import route_names, waypoint_table

__all__ = ["RecorderTab", "make_waypoint", "save_route"]

NO_ROUTES = "No routes"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")


def make_waypoint(
    action: str, label: str, manual: tuple[str, str, str], live: Position
) -> Waypoint:
    """A waypoint for *action*: at the *manual* x, y, z when all are given, else at *live*.

    Raises:
        RouteError: For a missing label, bad numbers or an unknown position.
    """
    if action in ("label", "goto"):
        if not label.strip():
            raise RouteError(f"{action} needs a label")
        return Waypoint(0, 0, 0, action, label.strip())
    if all(v.strip() for v in manual):
        try:
            x, y, z = (int(v) for v in manual)
        except ValueError:
            raise RouteError("X, Y and Z must be whole numbers") from None
        return Waypoint(x, y, z, action)
    if (live.x, live.y, live.z) == (0, 0, 0):
        raise RouteError("Position unknown")
    return Waypoint(live.x, live.y, live.z, action)


def save_route(routes_dir: Path, name: str, waypoints: list[Waypoint]) -> Path:
    """Write *waypoints* as route *name* in *routes_dir*; the file path.

    Raises:
        RouteError: For a bad name or a route the bot would refuse.
    """
    if not _NAME_RE.match(name):
        raise RouteError("route name: letters, digits, space, '.', '_' or '-'")
    raw = {
        "name": name,
        "waypoints": [
            {"action": wp.action, "label": wp.label}
            if wp.action in ("label", "goto")
            else {"x": wp.x, "y": wp.y, "z": wp.z, "action": wp.action}
            for wp in waypoints
        ],
    }
    RouteFileLoader.parse(raw)
    path = Path(routes_dir) / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return path


class RecorderTab(Tab):
    """Capture waypoints from the live position and save bot routes."""

    title = "Recorder"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        self.lbl_pos = stat_label(parent, "Position")
        self.lbl_pos.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 2))
        self.lbl_count = stat_label(parent, "Waypoints")
        self.lbl_count.grid(row=1, column=0, sticky="ew", padx=10)
        self._pos = Position(0, 0, 0)
        self._waypoints: list[Waypoint] = []
        self._text = ""

        cap = section(parent, "Capture waypoints")
        cap.grid(row=2, column=0, sticky="nsew", padx=10, pady=8)
        cap_grid = ctk.CTkFrame(cap, fg_color="transparent")
        cap_grid.pack(fill="both", expand=True)
        cap_grid.grid_columnconfigure(1, weight=1)

        self.var_type = ctk.StringVar(value="walk")
        ctk.CTkLabel(cap_grid, text="Action").grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 0)
        )
        ctk.CTkOptionMenu(cap_grid, values=list(ACTIONS), variable=self.var_type).grid(
            row=0, column=1, sticky="ew", padx=10
        )
        self.var_label = ctk.StringVar(value="")
        self.var_x = ctk.StringVar(value="")
        self.var_y = ctk.StringVar(value="")
        self.var_z = ctk.StringVar(value="")
        for row, (text, var) in enumerate(
            (
                ("Label (label / goto)", self.var_label),
                ("Manual X", self.var_x),
                ("Manual Y", self.var_y),
                ("Manual Z (blank = live position)", self.var_z),
            ),
            start=1,
        ):
            ctk.CTkLabel(cap_grid, text=text).grid(row=row, column=0, sticky="w", padx=10)
            ctk.CTkEntry(cap_grid, textvariable=var).grid(row=row, column=1, sticky="ew", padx=10)

        btns = ctk.CTkFrame(cap_grid, fg_color="transparent")
        btns.grid(row=5, column=0, columnspan=2, sticky="ew", padx=6, pady=(8, 4))
        for col, (text, cmd) in enumerate(
            (("Add waypoint", self._add), ("Undo", self._undo), ("Clear", self._clear))
        ):
            ctk.CTkButton(btns, text=text, command=cmd).grid(row=0, column=col, padx=4, sticky="ew")
            btns.grid_columnconfigure(col, weight=1)

        self.wp_list = ctk.CTkTextbox(
            cap_grid, height=110, wrap="none", font=ctk.CTkFont(family=theme.MONO)
        )
        self.wp_list.grid(row=6, column=0, columnspan=2, sticky="nsew", padx=10, pady=(6, 10))
        self.wp_list.configure(state="disabled")

        rfile = section(parent, "Route file")
        rfile.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
        rfile_grid = ctk.CTkFrame(rfile, fg_color="transparent")
        rfile_grid.pack(fill="both", expand=True)
        rfile_grid.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(rfile_grid, text="Route name").grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 0)
        )
        self.var_name = ctk.StringVar(value="")
        ctk.CTkEntry(rfile_grid, textvariable=self.var_name, placeholder_text="e.g. rotworms").grid(
            row=0, column=1, sticky="ew", padx=10
        )
        ctk.CTkLabel(rfile_grid, text="Existing route").grid(row=1, column=0, sticky="w", padx=10)
        self.var_route = ctk.StringVar(value=NO_ROUTES)
        self.route_menu = ctk.CTkOptionMenu(rfile_grid, values=[NO_ROUTES], variable=self.var_route)
        self.route_menu.grid(row=1, column=1, sticky="ew", padx=10)

        btns2 = ctk.CTkFrame(rfile_grid, fg_color="transparent")
        btns2.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=(8, 4))
        for col, (text, cmd) in enumerate(
            (
                ("Save route", self._save),
                ("Load selected", self._load),
                ("Refresh list", self._refresh_routes),
            )
        ):
            ctk.CTkButton(btns2, text=text, command=cmd).grid(
                row=0, column=col, padx=4, sticky="ew"
            )
            btns2.grid_columnconfigure(col, weight=1)

        self.lbl_status = ctk.CTkLabel(rfile_grid, text="", anchor="w")
        self.lbl_status.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        self._refresh_routes()
        self._redraw_list()

    def _routes_dir(self) -> Path:
        return Path(load_real_config().routes_dir)

    def _redraw_list(self) -> None:
        text = waypoint_table(Route("recording", waypoints=self._waypoints))
        self.lbl_count.configure(text=f"Waypoints: {len(self._waypoints)}")
        if text == self._text:
            return
        self._text = text
        self.wp_list.configure(state="normal")
        self.wp_list.delete("1.0", "end")
        self.wp_list.insert("1.0", text)
        self.wp_list.configure(state="disabled")

    def _add_action(self, action: str) -> None:
        manual = (self.var_x.get(), self.var_y.get(), self.var_z.get())
        try:
            wp = make_waypoint(action, self.var_label.get(), manual, self._pos)
        except RouteError as exc:
            self._status(str(exc))
            return
        self._waypoints.append(wp)
        self._redraw_list()

    def _add(self) -> None:
        self._add_action(self.var_type.get())

    def _undo(self) -> None:
        if self._waypoints:
            self._waypoints.pop()
            self._redraw_list()

    def _clear(self) -> None:
        self._waypoints.clear()
        self._redraw_list()

    def _refresh_routes(self) -> None:
        names = route_names(self._routes_dir())
        self.route_menu.configure(values=names or [NO_ROUTES])
        if self.var_route.get() not in names:
            self.var_route.set(names[0] if names else NO_ROUTES)

    def _save(self) -> None:
        name = self.var_name.get().strip()
        try:
            path = save_route(self._routes_dir(), name, self._waypoints)
        except (OSError, RouteError) as exc:
            self._status(f"Save failed: {exc}")
            return
        self._status(f"Saved {path}")
        self._refresh_routes()
        self.var_route.set(name)

    def _load(self) -> None:
        name = self.var_route.get()
        if name == NO_ROUTES:
            self._status("No route to load")
            return
        try:
            route = RouteFileLoader.load(self._routes_dir() / f"{name}.json")
        except RouteError as exc:
            self._status(f"Load failed: {exc}")
            return
        self._waypoints = list(route.waypoints)
        self.var_name.set(name)
        self._redraw_list()
        self._status(f"Loaded {name} ({len(route.waypoints)} waypoints)")

    def _status(self, text: str) -> None:
        self.lbl_status.configure(text=text)

    def on_poll(self, snap: WorldState) -> None:
        self._pos = snap.position
        text = f"Position: {self._pos.x}, {self._pos.y}, {self._pos.z}"
        if self.lbl_pos.cget("text") != text:
            self.lbl_pos.configure(text=text)
