"""Calibration tab: take or load a screenshot, then walk the calibration steps.

The tab only draws what :class:`~moonlight.setup.calibration_steps.CalibrationSteps`
says. The zoom maths (screen box <-> screenshot box) lives in the pure
functions below so it is tested without a window (R8).

# doc: docs/prs/gui-calibration.md#calibration-steps
"""

from __future__ import annotations

import base64
import dataclasses
import tkinter as tk
from collections.abc import Mapping
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING

import customtkinter as ctk
import cv2
import numpy as np

from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config, picked_config
from moonlight.gui.tabs.base import Tab
from moonlight.perception.capture import CaptureError, ScreenCapture
from moonlight.setup.autofind import find_regions, find_ui_scale
from moonlight.setup.calibrate import (
    REGIONS,
    CalibrationError,
    CalibrationProfile,
    Point,
    Rect,
)
from moonlight.setup.calibration_steps import CalibrationSteps
from moonlight.setup.profile import ProfileManager

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState
    from moonlight.gui.state_bridge import GuiContext

__all__ = [
    "AREA_HELP",
    "CalibrationTab",
    "box_at",
    "drag_rect",
    "fit_scale",
    "has_reference_images",
    "load_old_profile",
    "margin_for",
    "parse_ui_scale",
    "summary_image",
    "to_canvas",
    "to_screenshot",
    "view_rect",
]

#: Smallest margin around a box in the zoomed view, in screenshot px.
MIN_MARGIN = 40
#: A drag shorter than this on screen (either side) counts as a click.
MIN_DRAG = 3
START_DELAY_MS = 3000
UI_SCALE_RANGE = (0.5, 3.0)
NO_REFERENCES = "No reference images, draw the boxes by hand"
KEY_HELP = "Enter keep  ·  drag redraw  ·  ←/→ move  ·  Backspace back  ·  Esc stop"

#: One plain sentence per area: exactly what to box.
AREA_HELP: dict[str, str] = {
    "hp_bar": "The green HP bar at the top. Box it from its left end to its right end.",
    "mp_bar": "The blue mana bar next to the HP bar. Box it from its left end to its right end.",
    "battle_list": "The battle list: the rows of creatures, without the window's title bar.",
    "minimap": "The minimap: only the square map, without the buttons around it.",
    "floor_indicator": "The floor level control next to the minimap.",
    "status_icons": "The row of small condition icons (poisoned, hasted and so on).",
    "game_grid": "The whole game view, 15 x 11 tiles, corner to corner.",
}


# -- zoom maths (pure, tested) ---------------------------------------------


def margin_for(box: Rect) -> int:
    """Margin around *box*: half its bigger side, at least ``MIN_MARGIN``."""
    return max(max(box[2], box[3]) // 2, MIN_MARGIN)


def view_rect(box: Rect | None, size: tuple[int, int], margin: int) -> Rect:
    """The part of the screenshot to show: *box* plus *margin*, clamped.

    ``None`` gives the whole screenshot. *size* is ``(w, h)``.
    """
    w, h = size
    if box is None:
        return (0, 0, w, h)
    x, y, bw, bh = box
    x0 = min(max(x - margin, 0), w - 1)
    y0 = min(max(y - margin, 0), h - 1)
    x1 = min(max(x + bw + margin, x0 + 1), w)
    y1 = min(max(y + bh + margin, y0 + 1), h)
    return (x0, y0, x1 - x0, y1 - y0)


def fit_scale(view: Rect, canvas: tuple[int, int]) -> float:
    """Screen px per screenshot px so *view* fills *canvas* ``(w, h)``."""
    return min(canvas[0] / view[2], canvas[1] / view[3])


def to_screenshot(point: tuple[float, float], view: Rect, scale: float) -> Point:
    """Canvas point -> screenshot point, for *view* drawn at (0, 0) with *scale*."""
    return (round(view[0] + point[0] / scale), round(view[1] + point[1] / scale))


def to_canvas(box: Rect, view: Rect, scale: float) -> tuple[float, float, float, float]:
    """Screenshot box -> canvas corners ``(x0, y0, x1, y1)``."""
    x, y, w, h = box
    x0, y0 = (x - view[0]) * scale, (y - view[1]) * scale
    return (x0, y0, x0 + w * scale, y0 + h * scale)


def drag_rect(
    start: tuple[float, float],
    end: tuple[float, float],
    view: Rect,
    scale: float,
    size: tuple[int, int],
) -> Rect | None:
    """A canvas drag as a screenshot box: normalized and clamped.

    ``None`` when the drag is a click or ends up empty after clamping.
    """
    if abs(end[0] - start[0]) < MIN_DRAG or abs(end[1] - start[1]) < MIN_DRAG:
        return None
    (ax, ay), (bx, by) = to_screenshot(start, view, scale), to_screenshot(end, view, scale)
    x0, x1 = sorted(min(max(v, 0), size[0]) for v in (ax, bx))
    y0, y1 = sorted(min(max(v, 0), size[1]) for v in (ay, by))
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def box_at(point: Point, boxes: Mapping[str, Rect]) -> str | None:
    """Name of the smallest box holding *point*, or None."""
    px, py = point
    hits = [n for n, (x, y, w, h) in boxes.items() if x <= px < x + w and y <= py < y + h]
    return min(hits, key=lambda n: boxes[n][2] * boxes[n][3], default=None)


def parse_ui_scale(text: str) -> float | None:
    """*text* as a UI scale inside ``UI_SCALE_RANGE``, or None."""
    try:
        value = float(text.strip())
    except ValueError:
        return None
    low, high = UI_SCALE_RANGE
    return value if low <= value <= high else None


def has_reference_images(directory: Path) -> bool:
    """True when *directory* holds at least one PNG."""
    return directory.is_dir() and any(directory.glob("*.png"))


def load_old_profile(path: Path) -> CalibrationProfile | None:
    """The profile at *path*, or None when it is missing or unreadable."""
    try:
        return CalibrationProfile.load(path)
    except (OSError, ValueError, CalibrationError):
        return None


def summary_image(shot: np.ndarray, profile: CalibrationProfile) -> np.ndarray:
    """*shot* (BGR) with the profile's boxes and names drawn on a copy."""
    out = shot.copy()
    color = tuple(int(theme.LIFE[i : i + 2], 16) for i in (5, 3, 1))  # hex -> BGR
    for name, region in profile.regions.items():
        x, y, w, h = region.to_tuple
        cv2.rectangle(out, (x, y), (x + w - 1, y + h - 1), color, 2)
        cv2.putText(out, name, (x, max(y - 4, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    return out


def _photo(image: np.ndarray, scale: float) -> tk.PhotoImage:
    """*image* (BGR) resized by *scale* as a Tk image."""
    h, w = image.shape[:2]
    out = (max(round(w * scale), 1), max(round(h * scale), 1))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_NEAREST
    png = cv2.imencode(".png", cv2.resize(image, out, interpolation=interp))[1].tobytes()
    return tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))


# -- the tab -----------------------------------------------------------------


class CalibrationTab(Tab):
    """Start calibration / Load screenshot, then the steps on a canvas."""

    title = "Calibration"

    def __init__(self, ctx: GuiContext) -> None:
        super().__init__(ctx)
        self.shot: np.ndarray | None = None
        self.steps: CalibrationSteps | None = None
        self.path: Path | None = None
        #: Rough box for an area with no box, after the first drag.
        self.rough: Rect | None = None
        self.view: Rect = (0, 0, 1, 1)
        self.scale = 1.0
        self._photo: tk.PhotoImage | None = None
        self._summary: np.ndarray | None = None
        self._summary_photo: tk.PhotoImage | None = None
        self._drag: tuple[float, float] | None = None
        self._drag_item: int | None = None
        self._waiting = False

    def build(self, parent: ctk.CTkFrame) -> None:
        self.parent = parent
        self.idle = ctk.CTkFrame(parent, fg_color="transparent")
        self.idle.grid(row=0, column=0, sticky="nsew")
        row = ctk.CTkFrame(self.idle, fg_color="transparent")
        row.pack(anchor="w", padx=theme.PAD, pady=theme.PAD)
        self.btn_start = ctk.CTkButton(
            row, text="Start calibration", command=self.start,
            fg_color=theme.MANA, hover_color=theme.MANA_HOVER, text_color=theme.NIGHT,
        )
        self.btn_start.pack(side="left", padx=(0, theme.GAP))
        ctk.CTkButton(row, text="Load screenshot", command=self.load).pack(side="left")
        ctk.CTkLabel(row, text="Calibration").pack(side="left", padx=(theme.PAD, theme.UNIT))
        self.menu_profile = ctk.CTkOptionMenu(
            row, values=[""], command=self.pick_profile,
            fg_color=theme.RAISED, button_color=theme.RAISED,
            button_hover_color=theme.HOVER,
        )
        self.menu_profile.pack(side="left")
        ctk.CTkButton(row, text="New", width=64, command=self.new_profile).pack(
            side="left", padx=(theme.GAP, 0)
        )
        ctk.CTkButton(
            row, text="Delete", width=64, command=self.delete_profile,
            fg_color=theme.BLOOD, hover_color=theme.BLOOD_HOVER, text_color=theme.NIGHT,
        ).pack(side="left", padx=(theme.UNIT, 0))
        self.lbl_status = ctk.CTkLabel(self.idle, text="", anchor="w")
        self.lbl_status.pack(fill="x", padx=theme.PAD)
        anchors = load_real_config().templates_dir / "anchors"
        if not has_reference_images(anchors):
            ctk.CTkLabel(
                self.idle, text=NO_REFERENCES, anchor="w", text_color=theme.AMBER
            ).pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        self.lbl_summary = ctk.CTkLabel(
            self.idle, text="", anchor="w", text_color=theme.TEXT_DIM,
            font=ctk.CTkFont(size=theme.SIZE_BODY),
        )
        self.lbl_summary.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        self.summary = tk.Canvas(self.idle, bg=theme.NIGHT, highlightthickness=0, height=500)
        self.summary.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        self.summary.bind("<Configure>", lambda _e: self.render_summary())
        self.refill_profiles()
        self.ctx.config_listeners.append(self.refill_profiles)

        self.work = ctk.CTkFrame(parent, fg_color="transparent")
        self.work.grid(row=0, column=0, sticky="nsew")
        bar = ctk.CTkFrame(self.work, fg_color=theme.RAISED)
        bar.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 0))
        self.lbl_step = ctk.CTkLabel(
            bar, text="", anchor="w", text_color=theme.TEXT,
            font=ctk.CTkFont(size=theme.SIZE_HEADING, weight="bold"),
        )
        self.lbl_step.pack(fill="x", padx=theme.PAD, pady=(theme.GAP, 0))
        self.lbl_help = ctk.CTkLabel(
            bar, text="", anchor="w", justify="left", text_color=theme.TEXT,
            font=ctk.CTkFont(size=theme.SIZE_BODY), wraplength=900,
        )
        self.lbl_help.pack(fill="x", padx=theme.PAD)
        ctk.CTkLabel(
            bar, text=KEY_HELP, anchor="w", text_color=theme.TEXT,
            font=ctk.CTkFont(size=theme.SIZE_BODY),
        ).pack(fill="x", padx=theme.PAD, pady=(0, theme.GAP))
        self.review_bar = ctk.CTkFrame(self.work, fg_color="transparent")
        ctk.CTkLabel(self.review_bar, text="UI scale").pack(side="left", padx=(0, theme.UNIT))
        self.ent_scale = ctk.CTkEntry(self.review_bar, width=64, fg_color=theme.NIGHT)
        self.ent_scale.pack(side="left", padx=(0, theme.GAP))
        self.btn_save = ctk.CTkButton(
            self.review_bar, text="Save", command=self.save,
            fg_color=theme.LIFE, hover_color=theme.LIFE_HOVER, text_color=theme.NIGHT,
        )
        self.btn_save.pack(side="left")
        self.canvas = tk.Canvas(
            self.work, bg=theme.NIGHT, highlightthickness=0, takefocus=1, height=500
        )
        self.canvas.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        for event, handler in (
            ("<Return>", lambda _e: self._key_keep()),
            ("<BackSpace>", lambda _e: self._key_back()),
            ("<Escape>", lambda _e: self._key_escape()),
            ("<Left>", lambda _e: self._key_move(-1)),
            ("<Right>", lambda _e: self._key_move(1)),
            ("<ButtonPress-1>", self._press),
            ("<B1-Motion>", self._motion),
            ("<ButtonRelease-1>", self._release),
            ("<Configure>", lambda _e: self.render()),
        ):
            self.canvas.bind(event, handler)
        self.idle.tkraise()

    def on_poll(self, snap: WorldState) -> None:
        on = not self.ctx.bot_running() and not self._waiting
        self.btn_start.configure(
            state="normal" if on else "disabled",
            **theme.button_colors(on, theme.MANA, theme.MANA_HOVER),
        )

    # -- which calibration the bot uses -------------------------------------

    def refill_profiles(self) -> None:
        """Fill the dropdown with the saved calibrations; show the picked one."""
        cfg = load_real_config()
        names = ProfileManager(cfg.profiles_dir).list_profiles()
        if cfg.profile not in names:  # picked but not saved yet: show it anyway
            names = sorted([*names, cfg.profile])
        self.menu_profile.configure(values=names)
        self.menu_profile.set(cfg.profile)
        self.load_summary(ProfileManager(cfg.profiles_dir), cfg.profile)

    def pick_profile(self, name: str) -> None:
        """Make the bot use calibration *name* (a running bot restarts)."""
        path = picked_config()
        cfg = load_real_config(path)
        dataclasses.replace(cfg, profile=name).save(path)
        self.ctx.log.info(f"calibration: bot uses {name}")
        self.ctx.config_changed()
        self.refill_profiles()

    def new_profile(self) -> None:
        """Ask for a name, save it empty and pick it; Start calibration then fills it."""
        name = ctk.CTkInputDialog(text="Name for the new calibration:", title="New").get_input()
        if name is None:  # cancelled
            return
        name = name.strip()
        if not ProfileManager.valid_name(name):
            self.ctx.log.error(
                f"calibration: bad name {name!r}: use 1 to 40 letters, digits, _ and -"
            )
            return
        path = ProfileManager(load_real_config().profiles_dir).path(name)
        if path.exists():
            self.ctx.log.error(f"calibration: {name} already exists, pick it from the list")
            return
        CalibrationProfile((0, 0)).save(path)  # empty, so it stays in the list until calibrated
        self.pick_profile(name)

    def delete_profile(self) -> None:
        """Delete the picked calibration after asking, then pick another saved one."""
        cfg = load_real_config()
        if not messagebox.askyesno("Delete", f"Delete calibration {cfg.profile}?"):
            return
        manager = ProfileManager(cfg.profiles_dir)
        manager.delete(cfg.profile)
        self.ctx.log.info(f"calibration: deleted {cfg.profile}")
        rest = manager.list_profiles()
        if rest:
            self.pick_profile(rest[0])
        else:  # nothing left: keep the name, the next calibration saves to it
            self.refill_profiles()

    # -- summary of the picked calibration ----------------------------------

    def load_summary(self, manager: ProfileManager, name: str) -> None:
        """Draw *name*'s boxes on the screenshot it was calibrated on."""
        profile = load_old_profile(manager.path(name))
        shot_file = manager.shot_path(name)
        shot = cv2.imread(str(shot_file)) if shot_file.exists() else None
        if profile is None or shot is None:
            self._summary = None
            self.lbl_summary.configure(
                text=f"{name}: no calibration screenshot yet. Start calibration to make one."
            )
        else:
            self._summary = summary_image(shot, profile)
            self.lbl_summary.configure(
                text=f"{name}: {len(profile.regions)} of {len(REGIONS)} areas, "
                f"UI scale {profile.ui_scale:g}"
            )
        self.render_summary()

    def render_summary(self) -> None:
        self.summary.delete("all")
        self._summary_photo = None
        image = self._summary
        cw, ch = self.summary.winfo_width(), self.summary.winfo_height()
        if image is None or cw < 2 or ch < 2:
            return
        h, w = image.shape[:2]
        self._summary_photo = _photo(image, fit_scale((0, 0, w, h), (cw, ch)))
        self.summary.create_image(0, 0, image=self._summary_photo, anchor="nw")

    # -- getting a screenshot ------------------------------------------------

    def start(self) -> None:
        if self.ctx.bot_running() or self._waiting:
            return
        self._waiting = True
        self.lbl_status.configure(text="Switch to Tibia, screenshot in 3 seconds...")
        self.parent.after(START_DELAY_MS, self._capture)

    def _capture(self) -> None:
        self._waiting = False
        self.lbl_status.configure(text="")
        cap: ScreenCapture | None = None
        try:
            cap = ScreenCapture()
            frame = cap.get_frame()
        except CaptureError as exc:  # WindowNotFoundError is a CaptureError
            self.ctx.log.error(f"calibration: {exc}")
            return
        finally:
            if cap is not None:
                cap.close()
        # ScreenCapture gives RGB; the finders and the profile use BGR.
        self.open(np.ascontiguousarray(frame[:, :, ::-1]))

    def load(self) -> None:
        name = filedialog.askopenfilename(
            title="Load screenshot", filetypes=[("PNG image", "*.png")]
        )
        if not name:
            return
        image = cv2.imread(name)
        if image is None:
            self.ctx.log.error(f"calibration: could not read {name}")
            return
        self.open(image)

    def open(self, shot: np.ndarray) -> None:
        """Start the steps on *shot* (BGR)."""
        cfg = load_real_config()
        self.path = ProfileManager(cfg.profiles_dir).path(cfg.profile)
        old = load_old_profile(self.path)
        found = find_regions(shot)[0]
        scale, landmark = find_ui_scale(shot)
        self.shot = shot
        self.steps = CalibrationSteps((shot.shape[1], shot.shape[0]), found, old, landmark)
        # No Stop button found: keep the old profile's scale rather than a guessed 1.0.
        if landmark != (0, 0) or old is None:
            self.steps.ui_scale = scale
        else:
            self.steps.ui_scale = old.ui_scale
        self.rough = None
        self.ctx.log.info(f"calibration: found {', '.join(found) or 'nothing'}")
        self.work.tkraise()
        self.canvas.focus_set()
        self.render()

    def close(self) -> None:
        self.steps, self.shot, self.rough, self._photo = None, None, None, None
        self.canvas.delete("all")
        self.idle.tkraise()

    # -- drawing -----------------------------------------------------------

    def render(self) -> None:
        steps, shot = self.steps, self.shot
        if steps is None or shot is None:
            return
        size = steps.screenshot_size
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 2 or ch < 2:
            cw, ch = 800, 500
        if steps.in_review:
            base = None
        else:
            base = steps.box or self.rough
        self.view = view_rect(base, size, margin_for(base) if base else 0)
        self.scale = fit_scale(self.view, (cw, ch))
        x, y, w, h = self.view
        self._photo = _photo(shot[y : y + h, x : x + w], self.scale)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        if steps.in_review:
            self._render_review(steps)
        else:
            self._render_area(steps)

    def _render_area(self, steps: CalibrationSteps) -> None:
        self.review_bar.pack_forget()
        name = steps.area or ""
        self.lbl_step.configure(text=f"{REGIONS.index(name) + 1} of {len(REGIONS)}: {name}")
        hint = AREA_HELP[name]
        if steps.box is not None:
            self._box(steps.box, theme.MANA)
        elif self.rough is None:
            hint += " Not found: drag roughly around it to zoom in."
        else:
            hint += " Now drag the exact box."
        self.lbl_help.configure(text=hint)

    def _render_review(self, steps: CalibrationSteps) -> None:
        self.lbl_step.configure(text="Review")
        missing = [n for n in REGIONS if n not in steps.boxes]
        hint = "Click a box to fix it. Backspace goes back, Esc stops."
        if missing:
            hint += f"  |  missing: {', '.join(missing)}"
        self.lbl_help.configure(text=hint)
        for name, box in steps.boxes.items():
            color = theme.LIFE if name in steps.confirmed else theme.AMBER
            x0, y0, _, _ = self._box(box, color)
            self.canvas.create_text(
                x0 + 2, y0 - 2, text=name, anchor="sw", fill=color, font=(theme.MONO, 10)
            )
        if not self.review_bar.winfo_manager():
            self.ent_scale.delete(0, "end")
            self.ent_scale.insert(0, f"{steps.ui_scale:g}")
            self.review_bar.pack(fill="x", padx=theme.PAD, before=self.canvas)
        self.btn_save.configure(
            state="normal" if steps.can_save else "disabled",
            **theme.button_colors(steps.can_save, theme.LIFE, theme.LIFE_HOVER),
        )

    def _box(self, box: Rect, color: str) -> tuple[float, float, float, float]:
        corners = to_canvas(box, self.view, self.scale)
        self.canvas.create_rectangle(*corners, outline=color, width=2)
        return corners

    # -- input -------------------------------------------------------------

    def _press(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        self._drag = (event.x, event.y)
        self._drag_item = None

    def _motion(self, event: tk.Event) -> None:
        if self._drag is None or self.steps is None or self.steps.in_review:
            return
        if self._drag_item is None:
            self._drag_item = self.canvas.create_rectangle(
                *self._drag, event.x, event.y, outline=theme.AMBER, width=2, dash=(4, 2)
            )
        else:
            self.canvas.coords(self._drag_item, *self._drag, event.x, event.y)

    def _release(self, event: tk.Event) -> None:
        start, self._drag = self._drag, None
        steps = self.steps
        if start is None or steps is None:
            return
        end = (event.x, event.y)
        if steps.in_review:
            name = box_at(to_screenshot(end, self.view, self.scale), steps.boxes)
            if name is not None:
                steps.pick(name)
                self.rough = None
            self.render()
            return
        rect = drag_rect(start, end, self.view, self.scale, steps.screenshot_size)
        if rect is not None:
            if steps.box is None and self.rough is None:
                self.rough = rect  # first drag on a missing area only zooms
            else:
                steps.redraw(rect)
                self.rough = None
        self.render()

    def _key_keep(self) -> None:
        if self.steps is not None:
            self.steps.keep()
            self.rough = None
            self.render()

    def _key_back(self) -> None:
        if self.steps is not None:
            self.steps.back()
            self.rough = None
            self.render()

    def _key_move(self, step: int) -> None:
        if self.steps is not None:
            (self.steps.next if step > 0 else self.steps.prev)()
            self.rough = None
            self.render()

    def _key_escape(self) -> None:
        if self.steps is None:
            return
        answer = messagebox.askyesnocancel("Calibration", "Save what you have?")
        if answer is None:  # Cancel: stay where we were
            self.canvas.focus_set()
            return
        ok = answer and self._write(self.steps.save_partial())
        self.close()
        self._show_saved(ok)

    # -- saving ------------------------------------------------------------

    def _finished(self) -> CalibrationProfile | None:
        """The full profile with the typed UI scale, or None (logged) if not ready."""
        steps = self.steps
        if steps is None or not steps.can_save:
            return None
        scale = parse_ui_scale(self.ent_scale.get())
        if scale is None:
            low, high = UI_SCALE_RANGE
            self.ctx.log.error(f"calibration: UI scale must be a number from {low} to {high}")
            return None
        steps.ui_scale = scale
        return steps.save()

    def save(self) -> None:
        """Write the picked calibration."""
        profile = self._finished()
        if profile is None:
            return
        ok = self._write(profile)
        self.close()
        self._show_saved(ok)

    def _show_saved(self, ok: bool) -> None:
        self.refill_profiles()
        if ok:
            self.lbl_status.configure(text=f"Saved to {self.path}")

    def _write(self, profile: CalibrationProfile) -> bool:
        if self.path is None:
            return False
        try:
            profile.save(self.path)
            if self.shot is not None:  # the summary is drawn on it
                cv2.imwrite(str(self.path.with_suffix(".png")), self.shot)
        except OSError as exc:
            self.ctx.log.error(f"calibration: could not save {self.path}: {exc}")
            return False
        self.ctx.log.info(f"profile saved to {self.path}")
        self.ctx.config_changed(restart=True)  # the bot re-reads the layout
        return True
