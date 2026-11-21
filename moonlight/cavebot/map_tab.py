"""Map tab: the whole game map with the Cavebot tab's route on it, editable.

Left drag pans, the wheel zooms, ▲/▼ change the floor. A left click on a
waypoint selects it, a left drag moves it. Right click: on an empty sqm
"Add <kind> here" (after the selected waypoint; a kind with a label takes
the Cavebot tab's Label box), on a waypoint its kind or Delete. Undo (or
Ctrl+Z) takes back the last map edit. The list on the right shows every
waypoint; a click there selects it and moves the map to it. Monsters shows
the spawn sqm from ``data/spawns.json``, only those whose name holds the
typed text. The route is the Cavebot tab's list itself, so both tabs show
every edit at once; the Cavebot tab's Save writes it.

# doc: docs/prs/map.md#map-tab
# doc: docs/prs/map.md#map-sync
# doc: docs/prs/route-tools.md#map-editing
"""

from __future__ import annotations

import tkinter as tk
from functools import partial
from typing import TYPE_CHECKING

import customtkinter as ctk
from PIL import Image, ImageTk

from moonlight.cavebot.cavebot import ACTIONS, RouteError, Waypoint, has_tile
from moonlight.cavebot.recorder import make_waypoint
from moonlight.cavebot.route_map import MapTiles, MapView, Spot, bad_legs, leg_path, waypoint_at
from moonlight.cavebot.route_plan import SPAWNS_FILE, spawn_points
from moonlight.core.state import Position
from moonlight.core.wiring import MAP_DIR, walk_map_walkable
from moonlight.gui import theme
from moonlight.gui.tab_base import Label, Row, Tab

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.cavebot.tab import CavebotTab
    from moonlight.core.state import WorldState

__all__ = ["MapTab", "UndoStack", "matching_spawns"]

#: Shown first without a route or a known position (Thais).
START_SPOT = (32369, 32220, 7)
#: A press that moves less than this many pixels is a click, not a drag.
CLICK_PIXELS = 4
#: Kinds the map adds on a sqm (label, goto, check and deposit have no sqm).
MAP_KINDS = tuple(a for a in ACTIONS if a not in ("label", "goto", "check", "deposit"))


def tooltip(index: int, wp: Waypoint) -> str:
    """The hover and list text of waypoint *index* (0-based; shown 1-based like the Cavebot tab)."""
    where = f"  ({wp.x}, {wp.y}, {wp.z})" if has_tile(wp) else ""
    return f"{index + 1}  {wp.action}{where}  {wp.label}".rstrip()


def matching_spawns(spawns: dict[Spot, list[str]], typed: str) -> dict[Spot, list[str]]:
    """The spawn sqm with a monster whose name holds *typed* (any case); all when empty."""
    typed = typed.strip().lower()
    if not typed:
        return spawns
    return {spot: names for spot, names in spawns.items()
            if any(typed in name.lower() for name in names)}  # fmt: skip


# doc: docs/prs/route-tools.md#map-editing
class UndoStack:
    """Map edits as (before, after) waypoint lists; Undo restores *before*.

    An edit made elsewhere (the Cavebot tab, loading a route) leaves the list
    unlike the last *after*: then the stack no longer fits and is dropped.
    """

    def __init__(self) -> None:
        self._edits: list[tuple[list[Waypoint], list[Waypoint]]] = []

    def push(self, before: list[Waypoint], after: list[Waypoint]) -> None:
        self._edits.append((before, list(after)))

    def undo(self, waypoints: list[Waypoint]) -> bool:
        """Put the last edit's *before* into *waypoints*; False when there is none that fits."""
        if not self._edits or self._edits[-1][1] != waypoints:
            self._edits.clear()
            return False
        waypoints[:] = self._edits.pop()[0]
        return True


class MapTab(Tab):
    """The map, the route on it and the live character."""

    title = "Map"
    scrolls = False
    #: The Cavebot tab whose route this map shows and edits (set by the app).
    editor: CavebotTab

    def build(self, parent: ctk.CTkFrame) -> None:
        bar = Row(parent)
        bar.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, theme.UNIT))
        for arrow, by in (("▲", -1), ("▼", 1)):
            ctk.CTkButton(
                bar, text=arrow, width=32, fg_color=theme.RAISED, hover_color=theme.HOVER,
                command=lambda by=by: self._floor(by),
            ).pack(side="left", padx=(0, theme.UNIT))  # fmt: skip
        ctk.CTkButton(
            bar, text="Undo", width=56, fg_color=theme.RAISED, hover_color=theme.HOVER,
            command=self._undo_edit,
        ).pack(side="left", padx=(theme.UNIT, 0))  # fmt: skip
        self.lbl_where = Label(bar, text="", anchor="w")
        self.lbl_where.pack(side="left", padx=theme.PAD)
        self.chk_follow = ctk.CTkCheckBox(bar, text="Follow me")
        self.chk_follow.pack(side="right")
        self.ent_monster = ctk.CTkEntry(bar, width=120, placeholder_text="monster name")
        self.ent_monster.pack(side="right", padx=theme.UNIT)
        self.ent_monster.bind("<KeyRelease>", lambda _e: self._monsters_changed())
        self.chk_monsters = ctk.CTkCheckBox(bar, text="Monsters", command=self._monsters_changed)
        self.chk_monsters.pack(side="right", padx=theme.UNIT)
        self.lbl_msg = Label(parent, text="", anchor="w", text_color=theme.AMBER)
        self.lbl_msg.pack(fill="x", padx=theme.PAD)

        body = Row(parent)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(0, theme.PAD))
        self.lst_waypoints = tk.Listbox(
            body, width=30, bg=theme.PANEL, fg=theme.TEXT, selectbackground=theme.HOVER,
            highlightthickness=0, borderwidth=0, activestyle="none", exportselection=False,
            font=(theme.MONO, 10),
        )  # fmt: skip
        self.lst_waypoints.pack(side="right", fill="y", padx=(theme.UNIT, 0))
        self.lst_waypoints.bind("<<ListboxSelect>>", self._list_clicked)
        self.canvas = tk.Canvas(body, bg="black", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(side="left", fill="both", expand=True)
        self._image_id = self.canvas.create_image(0, 0, anchor="nw")
        self._photo: ImageTk.PhotoImage | None = None
        self.lbl_tip = tk.Label(self.canvas, bg=theme.PANEL, fg=theme.TEXT, justify="left")

        self.view = MapView(*START_SPOT)
        self._centred = False
        self.tiles = MapTiles(MAP_DIR)
        walkable = walk_map_walkable(MAP_DIR)
        self._path = leg_path(walkable) if walkable is not None else None
        self._me: tuple[int, int, int] | None = None
        self._drawn: object = None
        self._listed: tuple[Waypoint, ...] | None = None
        self._undo = UndoStack()
        #: All spawn sqm (loaded on the first Monsters tick) and those shown.
        self._spawns: dict[Spot, list[str]] | None = None
        self._shown_spawns: dict[Spot, list[str]] = {}
        #: The left press: where, and the waypoint under it (dragged) or None (pan).
        self._press: tuple[int, int] | None = None
        self._last_drag = (0, 0)
        self._dragging: int | None = None
        self._before_drag: list[Waypoint] = []
        self._moved = False

        c = self.canvas
        c.bind("<Configure>", self._resized)
        c.bind("<ButtonPress-1>", self._pressed)
        c.bind("<B1-Motion>", self._dragged)
        c.bind("<ButtonRelease-1>", self._released)
        c.bind("<Button-3>", self._menu)
        c.bind("<Motion>", self._hover)
        c.bind("<Leave>", lambda _e: self.lbl_tip.place_forget())
        c.bind("<Control-z>", lambda _e: self._undo_edit())
        c.bind("<MouseWheel>", lambda e: self._zoom(1 if e.delta > 0 else -1, e))
        c.bind("<Button-4>", lambda e: self._zoom(1, e))  # Linux wheel
        c.bind("<Button-5>", lambda e: self._zoom(-1, e))

    # -- drawing -----------------------------------------------------------

    @property
    def _waypoints(self) -> list[Waypoint]:
        return self.editor.route_waypoints

    def on_poll(self, snap: WorldState) -> None:
        pos = snap.position
        self._me = (pos.x, pos.y, pos.z) if pos.x else None
        if not self._centred:
            self._centre_on_start()
        if self.chk_follow.get() and self._me is not None:
            self.view.x, self.view.y, self.view.z = self._me
        self._draw()

    def _centre_on_start(self) -> None:
        """First look: the route's first waypoint, else the character."""
        spot = next(((wp.x, wp.y, wp.z) for wp in self._waypoints if has_tile(wp)), self._me)
        if spot is not None:
            self.view.x, self.view.y, self.view.z = spot
            self._centred = True

    def _draw(self) -> None:
        v = self.view
        selected = self.editor.selected_waypoint
        waypoints = tuple(self._waypoints)
        self._fill_list(waypoints, selected)
        key = (v.x, v.y, v.z, v.scale, v.width, v.height, waypoints, selected, self._me,
               id(self._shown_spawns))  # fmt: skip
        if key == self._drawn:
            return
        self._drawn = key
        bad = bad_legs(self._waypoints, self._path) if self._path is not None else []
        image = v.render(self.tiles, self._waypoints, bad, selected=selected, me=self._me,
                         path=self._path, monsters=self._shown_spawns)  # fmt: skip
        self._photo = ImageTk.PhotoImage(Image.fromarray(image))
        self.canvas.itemconfigure(self._image_id, image=self._photo)

    def _fill_list(self, waypoints: tuple[Waypoint, ...], selected: int | None) -> None:
        """The list on the right: every waypoint, the selected one highlighted."""
        lst = self.lst_waypoints
        if waypoints != self._listed:
            self._listed = waypoints
            lst.delete(0, "end")
            for i, wp in enumerate(waypoints):
                lst.insert("end", tooltip(i, wp))
        if self._list_row() != selected:
            lst.selection_clear(0, "end")
            if selected is not None and selected < len(waypoints):
                lst.selection_set(selected)
                lst.see(selected)

    def _list_row(self) -> int | None:
        rows = self.lst_waypoints.curselection()  # type: ignore[no-untyped-call]
        return int(rows[0]) if rows else None

    def _list_clicked(self, _event: tk.Event) -> None:
        i = self._list_row()
        if i is None:
            return
        self.editor.map_selected(i)
        wp = self._waypoints[i]
        if has_tile(wp):
            self.view.x, self.view.y, self.view.z = wp.x, wp.y, wp.z
            self.chk_follow.deselect()
            self._show_where(None)
        self._draw()

    def _monsters_changed(self) -> None:
        if not self.chk_monsters.get():
            self._shown_spawns = {}
        else:
            if self._spawns is None:
                if not SPAWNS_FILE.exists():
                    refresh = "run python tools/map.py refresh"
                    self.lbl_msg.configure(text=f"No {SPAWNS_FILE}: {refresh}")
                    self.chk_monsters.deselect()
                    return
                self._spawns = spawn_points(SPAWNS_FILE)
            self._shown_spawns = matching_spawns(self._spawns, str(self.ent_monster.get()))
        self._draw()

    def _resized(self, event: tk.Event) -> None:
        self.view.width, self.view.height = max(event.width, 1), max(event.height, 1)
        self._draw()

    def _floor(self, by: int) -> None:
        self.view.z = min(max(self.view.z + by, 0), 15)
        self.chk_follow.deselect()
        self._draw()
        self._show_where(None)

    def _zoom(self, steps: int, event: tk.Event) -> None:
        self.view.zoom(steps, event.x, event.y)
        self._draw()

    # -- mouse -------------------------------------------------------------

    def _sqm(self, event: tk.Event) -> tuple[int, int, int]:
        x, y = self.view.sqm_at(event.x, event.y)
        return x, y, self.view.z

    def _show_where(self, event: tk.Event | None) -> None:
        if event is None:
            text = f"Floor {self.view.z}"
        else:
            x, y, z = self._sqm(event)
            text = f"Floor {z}   sqm ({x}, {y}, {z})"
        self.lbl_where.configure(text=text)

    def _hover(self, event: tk.Event) -> None:
        self._show_where(event)
        sqm = self._sqm(event)
        i = waypoint_at(self._waypoints, *sqm)
        if i is not None:
            text = tooltip(i, self._waypoints[i])
        elif sqm in self._shown_spawns:
            text = ", ".join(self._shown_spawns[sqm])
        else:
            self.lbl_tip.place_forget()
            return
        self.lbl_tip.configure(text=text)
        self.lbl_tip.place(x=event.x + 14, y=event.y + 14)

    def _editable(self) -> bool:
        if self.editor.edit_locked:
            self.lbl_msg.configure(text="Turn Cavebot off to edit the route")
            return False
        return True

    def _edited(self, before: list[Waypoint], selected: int | None) -> None:
        """A map edit changed the route from *before*: remember it for Undo, tell the editor."""
        self._undo.push(before, self._waypoints)
        self.editor.map_edited(selected)
        self._draw()

    def _undo_edit(self) -> None:
        if not self._editable():
            return
        if not self._undo.undo(self._waypoints):
            self.lbl_msg.configure(text="Nothing to undo")
            return
        selected = self.editor.selected_waypoint
        if selected is not None and selected >= len(self._waypoints):
            selected = len(self._waypoints) - 1 if self._waypoints else None
        self.editor.map_edited(selected)
        self.lbl_msg.configure(text="Undone")
        self._draw()

    def _pressed(self, event: tk.Event) -> None:
        self.canvas.focus_set()  # for Ctrl+Z
        self._press = self._last_drag = (event.x, event.y)
        self._moved = False
        self._dragging = waypoint_at(self._waypoints, *self._sqm(event))
        self._before_drag = list(self._waypoints)

    def _dragged(self, event: tk.Event) -> None:
        if self._press is None:
            return
        px, py = self._press
        if not self._moved and max(abs(event.x - px), abs(event.y - py)) < CLICK_PIXELS:
            return
        self._moved = True
        i = self._dragging
        if i is None:  # pan
            lx, ly = self._last_drag
            self.view.pan(event.x - lx, event.y - ly)
            self.chk_follow.deselect()
        elif self._editable():
            x, y, _ = self._sqm(event)
            wp = self._waypoints[i]
            if (wp.x, wp.y) != (x, y):
                self._waypoints[i] = Waypoint(x, y, wp.z, wp.action, wp.label)
        self._last_drag = (event.x, event.y)
        self._draw()

    def _released(self, _event: tk.Event) -> None:
        if self._press is None:
            return
        self._press = None
        i = self._dragging
        if i is None:
            return  # a pan, or a click on an empty sqm: nothing to do
        if not self._moved:
            self.editor.map_selected(i)
            self._draw()
        elif self._waypoints != self._before_drag:
            self._edited(self._before_drag, i)  # the drag moved it

    def _add(self, kind: str, sqm: tuple[int, int, int]) -> None:
        """Add a *kind* waypoint on *sqm* after the selected one (else at the end)."""
        if not self._editable():
            return
        try:
            wp = make_waypoint(kind, self.editor.typed_label(), Position(*sqm))
        except RouteError as exc:
            self.lbl_msg.configure(text=f"{exc} (type it in the Cavebot tab's Label box)")
            return
        before = list(self._waypoints)
        selected = self.editor.selected_waypoint
        at = len(self._waypoints) if selected is None else selected + 1
        self._waypoints.insert(at, wp)
        self.lbl_msg.configure(text=f"Added {kind} {at + 1}")
        self._edited(before, at)

    def _menu(self, event: tk.Event) -> None:
        sqm = self._sqm(event)
        i = waypoint_at(self._waypoints, *sqm)
        menu = tk.Menu(self.canvas, tearoff=False)
        if i is None:
            for kind in MAP_KINDS:
                menu.add_command(label=f"Add {kind} here", command=partial(self._add, kind, sqm))
        else:
            for kind in MAP_KINDS:
                menu.add_command(label=kind, command=partial(self._set_kind, i, kind))
            menu.add_separator()
            menu.add_command(label="Delete", command=partial(self._delete, i))
        menu.tk_popup(event.x_root, event.y_root)

    def _set_kind(self, index: int, kind: str) -> None:
        if not self._editable():
            return
        wp = self._waypoints[index]
        label = wp.label or self.editor.typed_label()
        try:
            new = make_waypoint(kind, label, Position(wp.x, wp.y, wp.z))
        except RouteError as exc:
            self.lbl_msg.configure(text=f"{exc} (type it in the Cavebot tab's Label box)")
            return
        before = list(self._waypoints)
        self._waypoints[index] = new
        self._edited(before, index)

    def _delete(self, index: int) -> None:
        from moonlight.cavebot.tab import delete_waypoint

        if not self._editable():
            return
        before = list(self._waypoints)
        self._edited(before, delete_waypoint(self._waypoints, index))
