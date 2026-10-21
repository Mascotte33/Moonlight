"""Recorder tab (T39) — waypoint capture and route export.

Collects waypoints (from the live minimap position or manually typed
x/y) and saves them as JSON route files via :mod:`moonlight.gui.routes`.
Saved routes live in ``data/routes/<name>.json`` and can be reloaded or
driven by the cavebot.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui import routes, theme
from moonlight.gui.tabs.base import Tab, section, stat_label

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState

__all__ = ["RecorderTab"]


class RecorderTab(Tab):
    """Capture waypoints from the live minimap and export JSON routes."""

    title = "Recorder"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        self.lbl_pos = stat_label(parent, "Minimap position")
        self.lbl_pos.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 2))
        self.lbl_count = stat_label(parent, "Waypoints")
        self.lbl_count.grid(row=1, column=0, sticky="ew", padx=10)
        self._last_pos: tuple[int, int, int] | None = None
        self._waypoints: list[routes.Waypoint] = []
        self._list_signature: tuple[tuple[str, int, int], ...] = ()

        cap = section(parent, "Capture waypoints")
        cap.grid(row=2, column=0, sticky="nsew", padx=10, pady=8)
        cap_grid = ctk.CTkFrame(cap, fg_color="transparent")
        cap_grid.pack(fill="both", expand=True)
        cap_grid.grid_columnconfigure(1, weight=1)

        self.var_type = ctk.StringVar(value="walk")
        ctk.CTkLabel(cap_grid, text="Waypoint type").grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 0)
        )
        ctk.CTkOptionMenu(
            cap_grid, values=list(routes.WAYPOINT_TYPES), variable=self.var_type
        ).grid(row=0, column=1, sticky="ew", padx=10)

        self.var_x = ctk.StringVar(value="")
        self.var_y = ctk.StringVar(value="")
        ctk.CTkLabel(cap_grid, text="Manual X").grid(row=1, column=0, sticky="w", padx=10)
        ctk.CTkEntry(cap_grid, textvariable=self.var_x).grid(
            row=1, column=1, sticky="ew", padx=10
        )
        ctk.CTkLabel(cap_grid, text="Manual Y (blank = from minimap)").grid(
            row=2, column=0, sticky="w", padx=10
        )
        ctk.CTkEntry(cap_grid, textvariable=self.var_y).grid(
            row=2, column=1, sticky="ew", padx=10
        )

        btns = ctk.CTkFrame(cap_grid, fg_color="transparent")
        btns.grid(row=3, column=0, columnspan=2, sticky="ew", padx=6, pady=(8, 4))
        for col, (text, cmd) in enumerate(
            (
                ("Add waypoint", self._add),
                ("Undo", self._undo),
                ("Clear", self._clear),
            )
        ):
            ctk.CTkButton(btns, text=text, command=cmd).grid(
                row=0, column=col, padx=4, sticky="ew"
            )
            btns.grid_columnconfigure(col, weight=1)

        self.wp_list = ctk.CTkTextbox(cap_grid, height=110, wrap="none")
        self.wp_list.grid(
            row=4, column=0, columnspan=2, sticky="nsew", padx=10, pady=(6, 10)
        )
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
        ctk.CTkEntry(
            rfile_grid, textvariable=self.var_name, placeholder_text="e.g. mine_path"
        ).grid(row=0, column=1, sticky="ew", padx=10)

        ctk.CTkLabel(rfile_grid, text="Existing route").grid(
            row=1, column=0, sticky="w", padx=10
        )
        self.var_route = ctk.StringVar(value="--")
        self.route_menu = ctk.CTkOptionMenu(rfile_grid, values=["--"], variable=self.var_route)
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

        self.lbl_status = ctk.CTkLabel(
            rfile_grid, text="", anchor="w", font=ctk.CTkFont(size=theme.SIZE_TITLE)
        )
        self.lbl_status.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        self._refresh_routes()

    # -- waypoint list -----------------------------------------------------

    def _redraw_list(self) -> None:
        signature = tuple((w.type, w.x, w.y) for w in self._waypoints)
        if signature == self._list_signature:
            return
        self._list_signature = signature
        text = (
            "\n".join(
                f"{i + 1:3d}  {w.type:6s}  x={w.x}  y={w.y}"
                for i, w in enumerate(self._waypoints)
            )
            or "(no waypoints yet)"
        )
        self.wp_list.configure(state="normal")
        self.wp_list.delete("1.0", "end")
        self.wp_list.insert("1.0", text)
        self.wp_list.configure(state="disabled")
        self.lbl_count.configure(text=f"Waypoints: {len(self._waypoints)}")

    def _add(self) -> None:
        x_text = self.var_x.get().strip()
        y_text = self.var_y.get().strip()
        try:
            x = int(x_text)
            y = int(y_text) if y_text else None
        except ValueError:
            self._status("manual X/Y must be integers")
            return
        if x_text and y is None and self._last_pos is None:
            self._status("no minimap position yet — type X and Y")
            return
        if x_text and y is not None:
            point = (x, y)
        elif x_text and self._last_pos is not None:
            point = (x, self._last_pos[1])
        elif self._last_pos is not None:
            point = (self._last_pos[0], self._last_pos[1])
        else:
            self._status("no position source: waiting for a state snapshot")
            return
        self._waypoints.append(
            routes.Waypoint(
                type=self.var_type.get(), x=point[0], y=point[1], ts=time.time()
            )
        )
        self._redraw_list()

    def _undo(self) -> None:
        if self._waypoints:
            self._waypoints.pop()
            self._redraw_list()

    def _clear(self) -> None:
        self._waypoints.clear()
        self._redraw_list()

    # -- route files -------------------------------------------------------

    def _refresh_routes(self) -> None:
        names = routes.list_routes()
        self.route_menu.configure(values=["--"] + names)
        self.var_route.set("--" if self.var_route.get() not in names else self.var_route.get())

    def _save(self) -> None:
        name = self.var_name.get().strip()
        if not name:
            self._status("enter a route name first")
            return
        if not self._waypoints:
            self._status("no waypoints to save")
            return
        route = routes.Route(
            name=name, created_at=time.time(), waypoints=list(self._waypoints)
        )
        try:
            path = routes.save_route(route)
        except (OSError, ValueError) as exc:
            self._status(f"save failed: {exc}")
            return
        self._status(f"saved {path}")
        self._refresh_routes()
        self.var_route.set(route.name)

    def _load(self, _event: object | None = None) -> None:
        name = self.var_route.get()
        if not name or name == "--":
            self._status("pick a route from the list first")
            return
        try:
            route = routes.load_route(routes.route_path(name))
        except (OSError, ValueError) as exc:
            self._status(f"load failed: {exc}")
            return
        self._waypoints = list(route.waypoints)
        self.var_name.set(route.name)
        self._redraw_list()
        self._status(f"loaded {route.name} ({len(route.waypoints)} waypoints)")

    def _status(self, text: str) -> None:
        self.lbl_status.configure(text=text)

    # -- live updates ------------------------------------------------------

    def on_poll(self, snap: WorldState) -> None:
        pos = snap.position
        if pos is not None:
            self._last_pos = (pos.x, pos.y, pos.z)
            text = f"Minimap position: {pos.x}, {pos.y} (z {pos.z})"
            if self.lbl_pos.cget("text") != text:
                self.lbl_pos.configure(text=text)
