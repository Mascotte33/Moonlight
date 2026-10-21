"""Region calibration tool for MOONLIGHT (T10).

Calibrates screen regions for the Tibia client by letting the user draw them
on a screenshot, anchored to a *landmark* (a stable, always-present UI element
such as the "stop" button in the game window). This module never hard-codes
absolute screen coordinates: every region is stored as an offset *relative* to
the landmark. At runtime the bot re-locates the landmark (template match) and
derives each region as ``landmark_abs + region_relative``.

Design notes (from ``docs/ui_layout.md`` and T02/T09):
* **Why a landmark?** The game window moves when dragged and layouts differ
  between client versions. Only relative offsets and ratios are stable
  (see the "Master Landmark Map" in ``docs/ui_layout.md``).
* **Two layers, cleanly separated:**
  * The *core* (:class:`Region`, :class:`CalibrationProfile`, the coordinate
    transforms, save/load) is pure stdlib, headless, and fully unit-testable
    without OpenCV, a display, or a Tibia window.
  * The *interactive GUI* (:class:`CalibrationApp`, screen grab) imports
    ``cv2``/``mss``/``numpy`` lazily so the module stays importable everywhere
    and only the GUI path needs a window system.
* **Profiles** live in ``profiles/<name>.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from pathlib import Path

import mss
import numpy as np

__all__ = [
    "REGIONS",
    "GAME_GRID_COLS",
    "GAME_GRID_ROWS",
    "CalibrationError",
    "Region",
    "CalibrationProfile",
    "CalibrationApp",
    "region_to_relative",
    "region_to_absolute",
    "normalize_rect",
    "default_profiles_dir",
    "list_profiles",
    "main",
]

#: All region keys a calibration profile must define, in canonical order.
#:
#: ``status_icons`` is included even though some JSON examples omit it, because
#: ``docs/ui_layout.md`` lists it as a region to calibrate (buffs/conditions).
REGIONS: tuple[str, ...] = (
    "hp_bar",
    "mp_bar",
    "battle_list",
    "minimap",
    "floor_indicator",
    "status_icons",
    "game_grid",
)

#: The game window is a 15x11 grid of slots; the player sits at (7, 5).
GAME_GRID_COLS = 15
GAME_GRID_ROWS = 11

#: A point as ``(x, y)``.
Point = tuple[int, int]

#: A rectangle as ``(x, y, width, height)``.
Rect = tuple[int, int, int, int]


class CalibrationError(RuntimeError):
    """A calibration operation failed."""


def normalize_rect(x1: int, y1: int, x2: int, y2: int) -> Rect:
    """Normalize two corner points into ``(x, y, width, height)``.

    Handles reversed corners (dragging up-left) and returns zero-dimension
    rectangles for degenerate drags.
    """
    x = min(x1, x2)
    y = min(y1, y2)
    width = abs(x2 - x1)
    height = abs(y2 - y1)
    return (x, y, width, height)


def region_to_relative(region: Rect, landmark: Point) -> Rect:
    """Express an absolute region as an offset relative to *landmark*.

    Only the top-left corner is translated; size is unchanged.
    """
    x, y, width, height = region
    lx, ly = landmark
    return (x - lx, y - ly, width, height)


def region_to_absolute(region: Rect, landmark: Point) -> Rect:
    """Resolve a landmark-relative region back to absolute coordinates."""
    x, y, width, height = region
    lx, ly = landmark
    return (x + lx, y + ly, width, height)


class Region:
    """A named UI region stored as a landmark-relative rectangle.

    Attributes:
        x: Offset from the landmark's x.
        y: Offset from the landmark's y.
        width: Region width in pixels.
        height: Region height in pixels.
    """

    __slots__ = ("x", "y", "width", "height")

    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self.x = int(x)
        self.y = int(y)
        self.width = int(width)
        self.height = int(height)

    # -- construction -------------------------------------------------------

    @classmethod
    def from_tuple(cls, rect: Rect) -> Region:
        """Build from a ``(x, y, width, height)`` tuple."""
        x, y, width, height = rect
        return cls(x, y, width, height)

    @classmethod
    def from_list(cls, values: list[int]) -> Region:
        """Build from a 4-element list as read from a profile file."""
        if len(values) != 4:
            raise CalibrationError(f"Region must have 4 values, got {len(values)}")
        return cls(values[0], values[1], values[2], values[3])

    # -- accessors ----------------------------------------------------------

    @property
    def to_tuple(self) -> Rect:
        """Return as ``(x, y, width, height)``."""
        return (self.x, self.y, self.width, self.height)

    @property
    def to_list(self) -> list[int]:
        """Return as ``[x, y, width, height]`` for JSON serialisation."""
        return [self.x, self.y, self.width, self.height]

    @property
    def is_valid(self) -> bool:
        """Return True if the region has a strictly positive size."""
        return self.width > 0 and self.height > 0

    def absolute(self, landmark: Point) -> Rect:
        """Resolve this region to absolute coordinates at *landmark*."""
        return region_to_absolute(self.to_tuple, landmark)

    # -- dunder -------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Region):
            return NotImplemented
        return self.to_tuple == other.to_tuple

    def __hash__(self) -> int:
        return hash(self.to_tuple)

    def __repr__(self) -> str:
        return f"Region({self.x}, {self.y}, {self.width}, {self.height})"


class CalibrationProfile:
    """A named set of landmark-relative regions for one client layout.

    This is the on-disk and in-memory representation of a calibration. The
    landmark is recorded *absolutely* at calibration time (so the runtime knows
    what to look for), while every region is stored *relative* to it.

    Example:
        >>> profile = CalibrationProfile(landmark=(500, 300))
        >>> profile.set_region("hp_bar", Region(10, 12, 94, 23))
        >>> profile.landmark
        (500, 300)
    """

    def __init__(
        self,
        landmark: Point | None = None,
        regions: dict[str, Region] | None = None,
        window_size: tuple[int, int] | None = None,
        ui_scale: float = 1.0,
    ) -> None:
        self.landmark: Point = (0, 0) if landmark is None else (int(landmark[0]), int(landmark[1]))
        self.regions: dict[str, Region] = dict(regions) if regions else {}
        #: Game area ``(width, height)`` at calibration; ``None`` in old profiles.
        self.window_size = window_size
        self.ui_scale = ui_scale

    # -- construction -------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> CalibrationProfile:
        """Build a profile from a parsed JSON dict.

        Raises:
            CalibrationError: If required keys are missing or malformed.
        """
        if not isinstance(data, dict):
            raise CalibrationError("Profile root must be a JSON object")

        lm = data.get("landmark")
        if not isinstance(lm, (list, tuple)) or len(lm) != 2:
            raise CalibrationError("'landmark' must be a 2-element [x, y] list")
        landmark = (int(lm[0]), int(lm[1]))

        raw_regions = data.get("regions")
        if not isinstance(raw_regions, dict):
            raise CalibrationError("'regions' must be a JSON object")

        regions: dict[str, Region] = {}
        for name, value in raw_regions.items():
            if not isinstance(value, (list, tuple)) or len(value) != 4:
                raise CalibrationError(f"Region {name!r} must be [x, y, w, h]")
            regions[str(name)] = Region.from_list([int(v) for v in value])

        size = data.get("window_size")
        if size is not None and (not isinstance(size, (list, tuple)) or len(size) != 2):
            raise CalibrationError("'window_size' must be a 2-element [width, height] list")
        scale = data.get("ui_scale", 1.0)
        if not isinstance(scale, (int, float)) or scale <= 0:
            raise CalibrationError("'ui_scale' must be a positive number")

        return cls(
            landmark=landmark,
            regions=regions,
            window_size=None if size is None else (int(size[0]), int(size[1])),
            ui_scale=float(scale),
        )

    @classmethod
    def load(cls, path: str | Path) -> CalibrationProfile:
        """Load a profile from ``profiles/<name>.json``.

        Structural validation is performed (regions must be 4-element lists);
        call :meth:`validate` separately to require all regions present.

        Raises:
            CalibrationError: If the file is missing or malformed.
        """
        p = Path(path)
        if not p.exists():
            raise CalibrationError(f"Profile file not found: {p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CalibrationError(f"Could not read profile {p}: {exc}") from exc
        return cls.from_dict(data)

    # -- mutation -----------------------------------------------------------

    def set_region(self, name: str, region: Region) -> None:
        """Set or replace a region by name."""
        if name not in REGIONS:
            raise CalibrationError(f"Unknown region {name!r}; expected one of {REGIONS}")
        self.regions[name] = region

    # -- accessors ----------------------------------------------------------

    def get_region(self, name: str) -> Region:
        """Return a region by name.

        Raises:
            CalibrationError: If the region is not present.
        """
        try:
            return self.regions[name]
        except KeyError as exc:
            raise CalibrationError(f"Region {name!r} is not calibrated") from exc

    def region_absolute(self, name: str, current_landmark: Point) -> Rect:
        """Resolve a region to absolute coordinates at *current_landmark*."""
        return self.get_region(name).absolute(current_landmark)

    def game_grid_tiles(self, current_landmark: Point) -> tuple[int, int, int, int, int, int]:
        """Return the game-grid geometry for a landmark position.

        Returns:
            ``(grid_x, grid_y, grid_w, grid_h, tile_w, tile_h)`` where tile size
            is derived from the 15x11 grid (see ``docs/ui_layout.md``).
        """
        gx, gy, gw, gh = self.region_absolute("game_grid", current_landmark)
        tile_w = gw // GAME_GRID_COLS
        tile_h = gh // GAME_GRID_ROWS
        return (gx, gy, gw, gh, tile_w, tile_h)

    # -- validation ---------------------------------------------------------

    def validate(self) -> None:
        """Ensure the profile is complete and well-formed.

        Raises:
            CalibrationError: If any required region is missing or invalid.
        """
        missing = [name for name in REGIONS if name not in self.regions]
        if missing:
            raise CalibrationError(f"Profile is missing required regions: {missing}")
        for name, region in self.regions.items():
            if not region.is_valid:
                raise CalibrationError(f"Region {name!r} has non-positive size")

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-ready dict matching the profile schema."""
        return {
            "landmark": [self.landmark[0], self.landmark[1]],
            "regions": {
                name: region.to_list
                for name, region in self.regions.items()
            },
            "window_size": None if self.window_size is None else list(self.window_size),
            "ui_scale": self.ui_scale,
        }

    def save(self, path: str | Path) -> Path:
        """Write the profile to ``path``, creating parent dirs as needed.

        Returns:
            The resolved path written.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        return p

    # -- dunder -------------------------------------------------------------

    def __contains__(self, name: object) -> bool:
        return name in self.regions

    def __getitem__(self, name: str) -> Region:
        return self.get_region(name)

    def __iter__(self) -> Iterator[str]:
        return iter(self.regions)

    def __repr__(self) -> str:
        return f"CalibrationProfile(landmark={self.landmark}, regions={sorted(self.regions)})"


def default_profiles_dir() -> Path:
    """Return the ``profiles/`` directory next to the package."""
    return Path(__file__).resolve().parent.parent / "profiles"


def list_profiles(directory: str | Path | None = None) -> list[str]:
    """List profile names available in *directory* (default: profiles/)."""
    d = Path(directory) if directory is not None else default_profiles_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


# ---------------------------------------------------------------------------
# Interactive calibration GUI (lazy OpenCV / mss / numpy)
# ---------------------------------------------------------------------------


class CalibrationApp:
    """Interactive OpenCV window for drawing a calibration profile.

    Usage flow:
        1. The app shows a screenshot of the screen (or a provided image).
        2. The user *left-clicks* the landmark (e.g. the stop button).
        3. For each region in :data:`REGIONS`, the user *drags* a rectangle.
        4. On completion the app returns a :class:`CalibrationProfile`
           (regions already converted to landmark-relative offsets), or None
           if the user cancelled.

    Controls:
        * Left-drag: draw the current region rectangle.
        * Enter / Space: confirm the current region and advance.
        * Backspace / r: clear the current region (re-draw it).
        * Esc / q: cancel and exit.

    The GUI imports ``cv2``/``mss``/``numpy`` lazily so this module stays
    importable headless.
    """

    def __init__(
        self,
        image_bgr: np.ndarray | None = None,
        window_name: str = "MOONLIGHT Calibration",
    ) -> None:
        self._window_name = window_name
        if image_bgr is None:
            self._image: np.ndarray = grab_screen_bgr()
        else:
            arr = np.asarray(image_bgr)
            if arr.ndim != 3 or arr.shape[2] != 3:
                raise CalibrationError("image_bgr must be a (H, W, 3) array")
            self._image = arr.astype(np.uint8)

        # State.
        self._landmark: Point | None = None
        self._regions: dict[str, Rect] = {}
        self._region_idx = -1
        self._current_region: str | None = None
        self._dragging = False
        self._drag_start: Point | None = None
        self._last_move: Point | None = None
        self._done = False
        self._cancelled = False

    # -- mouse handling -----------------------------------------------------

    def _on_mouse(self, event: int, x: int, y: int, flags: int, _param: object) -> None:
        import cv2 as cv

        if self._done or self._cancelled:
            return

        # Track the cursor for a smooth live-drag preview.
        if event == cv.EVENT_MOUSEMOVE:
            self._last_move = (x, y)
            return

        if event == cv.EVENT_LBUTTONDOWN:
            if self._landmark is None:
                self._landmark = (x, y)
                self._advance_region()
            else:
                self._dragging = True
                self._drag_start = (x, y)
        elif event == cv.EVENT_LBUTTONUP:
            if (
                self._dragging
                and self._current_region is not None
                and self._drag_start is not None
            ):
                self._regions[self._current_region] = normalize_rect(
                    self._drag_start[0], self._drag_start[1], x, y
                )
                self._dragging = False
                self._drag_start = None

    # -- region stepping ----------------------------------------------------

    def _advance_region(self) -> None:
        self._region_idx += 1
        if self._region_idx >= len(REGIONS):
            self._done = True
        else:
            self._current_region = REGIONS[self._region_idx]

    # -- rendering ----------------------------------------------------------

    def _render(self) -> np.ndarray:
        import cv2 as cv

        img = self._image.copy()
        h, w = img.shape[:2]

        # Confirmed regions.
        for name, rect in self._regions.items():
            x, y, rw, rh = rect
            cv.rectangle(img, (x, y), (x + rw, y + rh), (0, 255, 255), 1)
            cv.putText(
                img, name, (x, max(0, y - 5)),
                cv.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1, cv.LINE_AA,
            )

        # Landmark crosshair.
        if self._landmark is not None:
            lx, ly = self._landmark
            cv.drawMarker(img, (lx, ly), (0, 255, 0), cv.MARKER_CROSS, 20, 2)

        # Live drag preview for the current region.
        if self._dragging and self._current_region is not None and self._drag_start is not None:
            sx, sy = self._drag_start
            ex, ey = self._last_move if self._last_move is not None else self._drag_start
            cv.rectangle(img, (sx, sy), (ex, ey), (255, 0, 0), 1, cv.LINE_AA)

        # Instruction banner.
        if self._landmark is None:
            msg = "Step 1: LEFT-CLICK the landmark (stop button). Esc to quit."
        elif self._done:
            msg = "All regions drawn."
        else:
            msg = (
                f"Drag to draw [{self._current_region}] | "
                f"Enter=confirm  Backspace=reroll  Esc=quit"
            )
        cv.rectangle(img, (0, 0), (w, 28), (0, 0, 0), -1)
        cv.putText(
            img, msg, (8, 19),
            cv.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv.LINE_AA,
        )
        return img

    # -- main loop ----------------------------------------------------------

    def run(self) -> CalibrationProfile | None:
        """Run the interactive loop; return a profile or None if cancelled.

        Returns:
            A :class:`CalibrationProfile` with landmark-relative regions, or
            None if the user cancelled or did not complete all regions.
        """
        import cv2 as cv

        cv.namedWindow(self._window_name, cv.WINDOW_NORMAL)
        cv.setMouseCallback(self._window_name, self._on_mouse)

        try:
            while not self._done and not self._cancelled:
                cv.imshow(self._window_name, self._render())
                key = cv.waitKey(30) & 0xFF

                if key in (27, ord("q")):  # Esc / q
                    self._cancelled = True
                elif key in (ord("\r"), ord("\n"), ord(" "), ord("\t")):
                    # Confirm current region.
                    if self._current_region is not None and self._current_region in self._regions:
                        self._advance_region()
                elif key in (ord("\b"), ord("r")):
                    # Redo current region.
                    if self._current_region is not None:
                        self._regions.pop(self._current_region, None)
                        self._dragging = False
                        self._drag_start = None
        finally:
            cv.destroyWindow(self._window_name)

        if self._cancelled or self._done is False or self._landmark is None:
            return None
        return self._build_profile()

    # -- profile construction ----------------------------------------------

    def _build_profile(self) -> CalibrationProfile:
        if self._landmark is None:
            raise CalibrationError("Landmark not set")
        profile = CalibrationProfile(landmark=self._landmark)
        for name, abs_rect in self._regions.items():
            rel_rect = region_to_relative(abs_rect, self._landmark)
            profile.set_region(name, Region.from_tuple(rel_rect))
        return profile


def grab_screen_bgr() -> np.ndarray:
    """Grab the primary screen as a BGR (H, W, 3) uint8 array via mss."""
    with mss.mss() as sct:
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        shot = sct.grab(monitor)
        arr = np.asarray(shot)  # BGRA (H, W, 4)
        return np.ascontiguousarray(arr[:, :, :3])


def load_image_bgr(path: str | Path) -> np.ndarray:
    """Load a BGR image from *path* (for offline calibration)."""
    import cv2 as cv

    img = cv.imread(str(path), cv.IMREAD_COLOR)
    if img is None:
        raise CalibrationError(f"Could not load image: {path}")
    return img


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the calibration tool."""
    parser = argparse.ArgumentParser(
        description="Calibrate MOONLIGHT screen regions relative to a landmark.",
    )
    parser.add_argument(
        "name", nargs="?", default=None,
        help="Profile name to save (e.g. 'default').",
    )
    parser.add_argument(
        "--screenshot", type=Path, default=None,
        help="Use an existing screenshot instead of capturing the live screen.",
    )
    parser.add_argument(
        "--list", action="store_true", dest="list_profiles",
        help="List existing profiles and exit.",
    )
    parser.add_argument(
        "--show", type=str, default=None,
        help="Print a saved profile and exit.",
    )
    args = parser.parse_args(argv)

    if args.list_profiles:
        names = list_profiles()
        if not names:
            print("No profiles found.")
        for n in names:
            print(n)
        return 0

    if args.show is not None:
        show_profile = CalibrationProfile.load(default_profiles_dir() / f"{args.show}.json")
        print(json.dumps(show_profile.to_dict(), indent=2))
        return 0

    if args.name is None:
        args.name = input("Profile name: ").strip() or "default"

    image = load_image_bgr(args.screenshot) if args.screenshot else None
    app = CalibrationApp(image_bgr=image)
    profile = app.run()
    if profile is None:
        print("Calibration cancelled.")
        return 1

    profile.validate()
    out = default_profiles_dir() / f"{args.name}.json"
    profile.save(out)
    print(f"Saved profile to {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    sys.exit(main())
