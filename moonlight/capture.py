"""Screen-capture backend for MOONLIGHT (T09).

Captures *only* the Tibia client window (never the whole screen) at a
physical-pixel resolution suitable for a ~20 Hz game loop, and reports whether
the client window is currently focused/visible so the loop can refuse to send
input while the player is watching.

Design notes (from T02, ``docs/hw_budget.md``):
* **Primary backend: ``mss``.** Simple, robust, and *always* returns the
  current screen content, so it never starves the loop. Measured 87-211 FPS at
  the 1280x960 client region -- far above the 20 Hz target even under heavy LLM
  contention.
* **Optional backend: ``dxcam``.** GPU-accelerated DXGI Desktop Duplication
  with ~10x lower per-frame latency (2 ms vs 20 ms). Windows-only, and it only
  yields *new* frames when the desktop presents them: on a static/paused
  desktop ``grab()`` returns ``None``. We handle that by reusing the last frame.

Window detection uses the Win32 ``user32`` API through ``ctypes`` (equivalent
to ``win32gui``/``pygetwindow`` but stdlib-only, so it stays importable and
type-checkable everywhere). Frames are returned as **RGB** ``(H, W, 3)``
``uint8`` arrays (OpenCV prefers BGR, but we store RGB consistently).
"""

from __future__ import annotations

import ctypes
import sys
import time
from collections import deque
from collections.abc import Sequence
from typing import Any

import mss
import numpy as np

__all__ = [
    "TIBIA_WINDOW_TITLE",
    "ScreenCapture",
    "CaptureError",
    "WindowNotFoundError",
]

#: Classic Tibia client window title. OTS servers may append text; matching is
#: exact-or-prefix, so this also matches "Tibia - <ServerName>".
TIBIA_WINDOW_TITLE = "Tibia"


#: Backends accepted by :class:`ScreenCapture`.
_BACKENDS = ("mss", "dxcam")


class CaptureError(RuntimeError):
    """A capture operation failed."""


class WindowNotFoundError(CaptureError):
    """The client window could not be located on screen."""


class _RECT(ctypes.Structure):
    """Win32 RECT structure (pixel coordinates)."""

    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def _set_dpi_aware() -> None:
    """Make the process DPI-aware so all geometry is in physical pixels.

    No-op off Windows. Tries per-monitor-v2 first (best for multi-monitor with
    mixed DPI), then falls back to system DPI awareness.
    """
    if sys.platform != "win32":
        return
    try:
        # PER_MONITOR_AWARE_V2 = -4 as a DPI_AWARENESS_CONTEXT handle.
        ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _find_window_hwnd(title: str) -> int | None:
    """Find a visible top-level window by title and return its handle.

    Tries an exact match (``FindWindowW``) first, then enumerates visible
    top-level windows and returns the first whose title equals *title* or
    starts with it (to cover "Tibia - <ServerName>"). Returns ``None`` if no
    match, or off Windows.
    """
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = ctypes.c_void_p

    hwnd = user32.FindWindowW(None, title)
    if hwnd:
        return int(hwnd)

    found: list[int] = []

    def _enum_cb(h: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(h):
            return True
        length = user32.GetWindowTextLengthW(h)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(h, buf, length + 1)
            wtitle = buf.value
            if wtitle == title or wtitle.startswith(title):
                found.append(int(h))
        return True

    # Keep a strong reference so the callback survives the EnumWindows call.
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
    )
    proc = EnumWindowsProc(_enum_cb)
    user32.EnumWindows(proc, None)

    return found[0] if found else None


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Return ``(left, top, width, height)`` for *hwnd*, or ``None``."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
    user32.GetWindowRect.restype = ctypes.c_bool
    rect = _RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return (rect.left, rect.top, width, height)


class ScreenCapture:
    """Capture the Tibia client window and report its focus state.

    Frames are RGB ``(H, W, 3)`` ``uint8`` NumPy arrays in physical pixels.

    Example:
        >>> cap = ScreenCapture()
        >>> if cap.is_focused():
        ...     frame = cap.get_frame()   # RGB (H, W, 3)
        ...     print(cap.get_stats()["fps"])
    """

    def __init__(
        self,
        backend: str = "mss",
        window_title: str = TIBIA_WINDOW_TITLE,
        device_idx: int = 0,
        output_idx: int | None = None,
    ) -> None:
        """Create a capture source.

        Args:
            backend: ``"mss"`` (default) or ``"dxcam"`` (Windows only).
            window_title: Client window title to locate (exact-or-prefix).
            device_idx: DXGI adapter index (dxcam only).
            output_idx: Output index on the adapter; ``None`` = primary.

        Raises:
            CaptureError: If an unknown backend is requested, or ``dxcam`` is
                requested on a non-Windows platform.
        """
        if backend not in _BACKENDS:
            raise CaptureError(f"Unknown backend {backend!r}; expected one of {_BACKENDS}")
        if backend == "dxcam" and sys.platform != "win32":
            raise CaptureError("The 'dxcam' backend is only available on Windows")

        self.backend = backend
        self.window_title = window_title
        self._device_idx = device_idx
        self._output_idx = output_idx

        self._sct: mss.MSS | None = None
        # dxcam has no type stubs and is loaded via importlib, so it is Any.
        self._dxcam_mod: Any = None
        self._dxcam_cam: Any = None
        self._last_frame: np.ndarray | None = None
        self._last_rect: tuple[int, int, int, int] | None = None

        # Statistics.
        self._frames_captured = 0
        self._new_frames = 0
        self._stale_frames = 0
        self._total_frame_time = 0.0
        self._last_frame_time_ms: float | None = None
        self._frame_times: deque[float] = deque()
        self._window_found = False
        self._closed = False

        _set_dpi_aware()

    # -- resource management -----------------------------------------------

    def _ensure_mss(self) -> mss.MSS:
        """Lazily create (and reuse) the ``mss`` screen instance."""
        if self._sct is None:
            self._sct = mss.MSS()
        return self._sct

    def close(self) -> None:
        """Release all capture resources. Safe to call multiple times."""
        if self._closed:
            return
        self._closed = True
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None
        if self._dxcam_cam is not None:
            try:
                self._dxcam_cam.release()
            except Exception:
                pass
            self._dxcam_cam = None
        self._dxcam_mod = None

    def __enter__(self) -> ScreenCapture:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    # -- window state -------------------------------------------------------

    def get_window_rect(self) -> tuple[int, int, int, int] | None:
        """Return the client window as ``(left, top, width, height)``.

        Coordinates are physical pixels. Returns ``None`` if the window is not
        currently found (the last known rect is still available via
        :attr:`_last_rect`).
        """
        hwnd = _find_window_hwnd(self.window_title)
        if hwnd is None:
            return None
        return _window_rect(hwnd)

    def is_focused(self) -> bool:
        """Return ``True`` if the client window is focused *and* visible.

        A window is "focused" here when it is the current foreground window.
        Off Windows (or if the window is not found) this returns ``False``.
        """
        if sys.platform != "win32":
            return False
        hwnd = _find_window_hwnd(self.window_title)
        if hwnd is None:
            return False
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        if not user32.IsWindowVisible(hwnd):
            return False
        return int(hwnd) == int(user32.GetForegroundWindow())

    # -- capture ------------------------------------------------------------

    def capture_region(
        self, x: int, y: int, width: int, height: int
    ) -> np.ndarray:
        """Capture a screen region and return it as RGB ``(H, W, 3)``.

        Coordinates are physical pixels. This is the low-level capture primitive;
        :meth:`get_frame` uses it for the client window. Uses the ``mss`` backend
        directly regardless of the configured backend.
        """
        return np.ascontiguousarray(self._grab_rgb_view(x, y, width, height))

    def _grab_rgb_view(self, x: int, y: int, width: int, height: int) -> np.ndarray:
        """One mss grab as an RGB *view* (not contiguous, no copy yet)."""
        if width <= 0 or height <= 0:
            raise CaptureError(f"Invalid region size {width}x{height}")
        sct = self._ensure_mss()
        region = {"left": x, "top": y, "width": width, "height": height}
        arr = np.asarray(sct.grab(region))  # (H, W, 3 or 4); BGR or BGRA from mss
        return arr[:, :, 2::-1]  # reverse the first three channels: BGR(A) -> RGB

    def _grab_dxcam(self, rect: tuple[int, int, int, int]) -> tuple[np.ndarray, bool]:
        """Grab one frame via dxcam; return ``(frame, is_new)``.

        dxcam yields ``None`` when the desktop presents no new frame. In that
        case we reuse the last frame we have (``is_new=False``).
        """
        mod = self._load_dxcam()
        if mod is None:
            raise CaptureError("dxcam backend requested but the module is not available")
        cam = self._get_dxcam_cam(mod)

        left, top, width, height = rect
        region = (left, top, left + width, top + height)
        # new_frame_only=True: None when there is no *new* desktop frame.
        frame = cam.grab(region=region, new_frame_only=True)

        if frame is not None:
            self._last_frame = np.asarray(frame)
            return self._last_frame, True

        if self._last_frame is not None:
            return self._last_frame, False

        raise CaptureError(
            "dxcam returned no frame and no previous frame is available "
            "(the desktop must present at least one frame first)"
        )

    def _load_dxcam(self) -> Any:
        """Lazily import dxcam (optional, Windows-only). Returns None if absent."""
        if self._dxcam_mod is not None:
            return self._dxcam_mod
        import importlib

        try:
            self._dxcam_mod = importlib.import_module("dxcam")
        except ModuleNotFoundError:
            self._dxcam_mod = None
        return self._dxcam_mod

    def _get_dxcam_cam(self, mod: Any) -> Any:
        """Lazily create (and reuse) the dxcam camera for this source."""
        if self._dxcam_cam is None:
            create_fn = getattr(mod, "create", None)
            if create_fn is None:
                raise CaptureError("dxcam module has no 'create' function")
            self._dxcam_cam = create_fn(
                device_idx=self._device_idx,
                output_idx=self._output_idx,
                output_color="RGB",
                backend="dxgi",
            )
        return self._dxcam_cam

    def get_frame(
        self, regions: Sequence[tuple[int, int, int, int]] | None = None
    ) -> np.ndarray:
        """Return the current client-window frame as RGB ``(H, W, 3)``.

        Uses the configured backend. For ``dxcam``, if no new frame has been
        presented, the last captured frame is returned instead.

        *regions* (P2): window-relative ``(x, y, w, h)`` rectangles. With
        ``mss`` only those are grabbed (one small grab each) into a
        window-sized black frame, so readers index it exactly like a full
        frame -- a few small grabs instead of one full-window grab. ``None``
        (or the ``dxcam`` backend, whose full grab is already ~2 ms) grabs
        the whole window: the fallback for uncalibrated regions.

        Raises:
            WindowNotFoundError: If the client window has never been found.
            CaptureError: If the capture backend fails.
        """
        if self._closed:
            raise CaptureError("ScreenCapture is closed")
        t0 = time.perf_counter()

        rect = self.get_window_rect()
        if rect is not None:
            self._last_rect = rect
            self._window_found = True
        elif self._last_rect is not None:
            # Window vanished but we remember where it was; capture that region.
            rect = self._last_rect
        else:
            raise WindowNotFoundError(
                f"Client window {self.window_title!r} not found on screen"
            )

        if self.backend == "dxcam":
            frame, is_new = self._grab_dxcam(rect)
            if is_new:
                self._new_frames += 1
            else:
                self._stale_frames += 1
        elif regions is not None:
            frame = self._grab_regions(rect, regions)
            self._new_frames += 1
        else:
            frame = self.capture_region(*rect)
            self._new_frames += 1
            self._last_frame = frame

        # Update statistics.
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self._frames_captured += 1
        self._total_frame_time += elapsed_ms
        self._last_frame_time_ms = elapsed_ms
        now = time.perf_counter()
        self._frame_times.append(now)
        cutoff = now - 2.0
        while self._frame_times and self._frame_times[0] < cutoff:
            self._frame_times.popleft()

        return frame

    def _grab_regions(
        self,
        rect: tuple[int, int, int, int],
        regions: Sequence[tuple[int, int, int, int]],
    ) -> np.ndarray:
        """Window-sized black frame with only *regions* captured (P2).

        One grab of the regions' bounding box (clipped to the window), not
        one per region: every mss grab pays a fixed ~16 ms GDI/compositor
        sync, so five small grabs cost more than one larger one (measured
        2560x1369: full 43 ms, bounding box 21 ms, five regions 84 ms).
        """
        left, top, width, height = rect
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        boxes = [
            (max(0, x), max(0, y), min(width, x + w), min(height, y + h))
            for x, y, w, h in regions
        ]
        boxes = [b for b in boxes if b[2] > b[0] and b[3] > b[1]]
        if not boxes:
            return frame
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        # Convert straight into the frame: one copy instead of two.
        frame[y0:y1, x0:x1] = self._grab_rgb_view(left + x0, top + y0, x1 - x0, y1 - y0)
        return frame

    def get_stats(self) -> dict[str, object]:
        """Return capture statistics as a dict.

        Keys:
            backend (str): Active backend name.
            frames_captured (int): Total frames returned by :meth:`get_frame`.
            new_frames (int): Frames that were genuinely new captures.
            stale_frames (int): dxcam frames reused (no new desktop frame).
            fps (float): Rolling capture rate over the last ~2 seconds.
            avg_frame_time_ms (float): Mean time per :meth:`get_frame` call.
            last_frame_time_ms (float | None): Most recent call time.
            window_found (bool): Whether the client window is currently found.
            window_rect (tuple | None): Last known window rect.
        """
        fps = 0.0
        if len(self._frame_times) > 1:
            span = self._frame_times[-1] - self._frame_times[0]
            if span > 0:
                fps = (len(self._frame_times) - 1) / span

        return {
            "backend": self.backend,
            "frames_captured": self._frames_captured,
            "new_frames": self._new_frames,
            "stale_frames": self._stale_frames,
            "fps": round(fps, 2),
            "avg_frame_time_ms": (
                round(self._total_frame_time / self._frames_captured, 3)
                if self._frames_captured
                else 0.0
            ),
            "last_frame_time_ms": self._last_frame_time_ms,
            "window_found": self._window_found,
            "window_rect": self._last_rect,
        }


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    cap = ScreenCapture()
    print(f"backend={cap.backend} window_title={cap.window_title!r}")
    print(f"window_rect={cap.get_window_rect()}")
    print(f"is_focused={cap.is_focused()}")
    try:
        frame = cap.get_frame()
        print(f"frame shape={frame.shape} dtype={frame.dtype}")
    except CaptureError as err:
        print(f"capture failed (expected if no client window): {err}")
    print(cap.get_stats())
    cap.close()
