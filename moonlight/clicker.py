"""Closed-loop click controller (T17).

The reliability-critical input path: move the mouse, *verify* where the
cursor actually landed (by reading the rendered cursor off the screen),
correct any drift, and click only once the position is verified within
tolerance.

Why closed loop
---------------

USB HID relative moves are not exact: the driver quantizes them, the game
snaps the cursor to its own grid, and the capture loop may serve a
slightly stale frame. An open-loop "move then blind click" therefore
clicks at wherever the cursor *actually* is — which can be off the
intended target. So after every move the controller re-reads the
*rendered* cursor position with a
:class:`moonlight.perception.cursor.CursorReader` and issues correcting
moves until the verified position is within ``tolerance`` of the target.

Convergence contract
--------------------

* A click is sent **exactly once, and only on convergence**: the last
  verified cursor position — read from the rendered cursor, never from
  the sent deltas — must satisfy ``max(|dx|, |dy|) <= tolerance``
  (infinity norm) with respect to *target*. The default tolerance is
  ±2 pixels (T17).
* If the cursor cannot be verified within the move budget (the chunks
  needed to cover the first measured distance plus ``max_iters``
  corrections), or is not found at all (``CursorReader.read`` returns
  ``None``), the click fails and **no click** is sent — a missed click
  is safer than a blind click at an unverified position (fail closed).
* Every wire ``M`` command stays within the protocol's -127..127 range
  (see :mod:`moonlight.input`).

Per-tick state machine (P5 / F-14)
----------------------------------

Production callers never block a tick on a click: they :meth:`request`
a click and call :meth:`step` once per loop tick. Each step does at most
**one capture and one move** (or the click): read the cursor, then send
one chunk toward the target. The first read searches the reader's full
cursor region (or, once the cursor was seen, a small ROI where it was
last verified); later reads search only a small ROI around where the
cursor should now be. A read or click never happens sooner than
``settle_ms`` after the last move (15-30 ms: the OS/game must render the
move; at 20 Hz the tick gap already covers it). :meth:`next_read` tells
the capture which region the next step reads, so production folds the
cursor ROI into the tick's one screen grab. A failed step (cursor
lost, move budget spent) drops the whole job queue, so a follow-up
click never fires after a failed first click. Moves and clicks dropped
by the wire rate limiter (F-20) are retried on the next step.
:meth:`click_at` is the blocking convenience (request + step until done)
for tools and tests.

**Open loop** (``ClickerConfig.open_loop``): whether the rendered cursor
can be captured at all is a T33 hardware-in-the-loop question. If it
cannot, the controller dead-reckons instead: it first homes the cursor
into the screen's top-left corner (``home_moves`` full negative moves),
then tracks the position from the moves it sent, scaled by the
calibrated ``mouse_scale`` (pixels per count; T33 calibrates it).

The controller takes a *frame provider* callable (in production it wraps
``moonlight.capture``; in tests it renders a synthetic cursor at the
:class:`moonlight.input.FakeInput` virtual cursor position) — no hard
dependency on the capture backend.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from moonlight.input import MAX_MOUSE_DELTA, ClickSide, InputBackend
from moonlight.perception.cursor import CursorReader, Rect

__all__ = [
    "ClickController",
    "ClickerConfig",
    "DEFAULT_TOLERANCE",
    "DEFAULT_MAX_ITERS",
]

logger = logging.getLogger("moonlight.clicker")

#: Default convergence tolerance in pixels (±2 px, T17 requirement).
DEFAULT_TOLERANCE = 2

#: Default maximum number of correcting moves after the initial move.
DEFAULT_MAX_ITERS = 5


@dataclass(frozen=True)
class ClickerConfig:
    """Clicker settings (``"clicker"`` in the bot config).

    Attributes:
        open_loop: Dead-reckon the cursor instead of reading it off the
            screen (use when T33 HIL shows the cursor is not capturable).
        mouse_scale: Pixels the cursor moves per wire count (open loop;
            calibrate on the server in T33).
        settle_ms: Minimum delay between a move and the next cursor read
            or click.
        roi_half: Half-size in pixels of the ROI searched around the
            expected cursor position after a move.
        home_moves: Full ``-127, -127`` moves that home the cursor into
            the screen corner before open-loop tracking starts.
    """

    open_loop: bool = False
    mouse_scale: float = 1.0
    settle_ms: float = 20.0
    roi_half: int = 48
    home_moves: int = 30

    def validate(self) -> None:
        if not self.mouse_scale > 0:
            raise ValueError(f"clicker.mouse_scale must be > 0, got {self.mouse_scale}")
        if not 0 <= self.settle_ms <= 100:
            raise ValueError(f"clicker.settle_ms must be in 0..100, got {self.settle_ms}")
        if self.roi_half < 8 or self.home_moves < 1:
            raise ValueError("clicker.roi_half must be >= 8 and clicker.home_moves >= 1")


@dataclass
class _Job:
    target: tuple[int, int]
    side: ClickSide
    tolerance: int
    max_iters: int
    budget: int | None = None  # moves left; set on the first read
    expected: tuple[int, int] | None = None  # where the cursor should be now


class ClickController:
    """Closed-loop click: move -> verify rendered cursor -> correct -> click.

    Example:
        >>> controller = ClickController(input_backend, reader, frame_provider)
        >>> controller.request((412, 233))
        >>> result = controller.step()  # once per tick; None while pending
    """

    def __init__(
        self,
        input_backend: InputBackend,
        cursor_reader: CursorReader,
        frame_provider: Callable[[], np.ndarray],
        *,
        config: ClickerConfig | None = None,
        roi_provider: Callable[[Rect], np.ndarray] | None = None,
        window_origin: Callable[[], tuple[int, int]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create a controller.

        Args:
            input_backend: Sends moves and clicks (``ArduinoInput`` behind
                the wire rate limiter in production, ``FakeInput`` in tests).
            cursor_reader: Reads the rendered cursor position off frames.
            frame_provider: Callable returning the current frame (RGB
                ``(H, W, 3)`` ``uint8``); used when *roi_provider* is None.
            config: Clicker settings (default: closed loop, 20 ms settle).
            roi_provider: Captures just a region: returns a frame in which
                at least that ``(x, y, w, h)`` is populated (P5: a small
                ROI grab instead of a full frame).
            window_origin: Screen ``(left, top)`` of the client window;
                open loop needs it to place the homed cursor.
            clock: Monotonic clock (settle timing).
            sleep_fn: Sleep used for the settle delay.
        """
        self._input = input_backend
        self._reader = cursor_reader
        self._frame_provider = frame_provider
        self._config = config or ClickerConfig()
        self._config.validate()
        self._roi_provider = roi_provider
        self._window_origin = window_origin
        self._clock = clock
        self._sleep = sleep_fn
        self._jobs: deque[_Job] = deque()
        self._last_move = float("-inf")
        #: Last verified cursor position: a new click's first read searches
        #: a small ROI there, the full cursor region only on a miss.
        self._seen: tuple[int, int] | None = None
        #: Open loop: dead-reckoned cursor position (None until homed).
        self._estimate: tuple[float, float] | None = None
        self._homed = 0

    # -- public API ---------------------------------------------------------

    @property
    def busy(self) -> bool:
        """True while a requested click is still in progress."""
        return bool(self._jobs)

    def request(
        self,
        target: tuple[int, int],
        *,
        tolerance: int = DEFAULT_TOLERANCE,
        max_iters: int = DEFAULT_MAX_ITERS,
        side: ClickSide = ClickSide.LEFT,
    ) -> None:
        """Queue a click at *target*; :meth:`step` drives it, once per tick.

        Args:
            target: ``(x, y)`` click target in frame (client-window)
                coordinates.
            tolerance: Maximum allowed distance (infinity norm, pixels)
                between the verified cursor and *target* before clicking.
            max_iters: Correcting moves allowed beyond the chunks needed
                to cover the first measured distance.
            side: Click button (default left).

        Raises:
            ValueError: If *target* is not a 2-element pair, or
                *tolerance*/*max_iters* are out of range.
        """
        tx, ty = self._as_point(target)
        if tolerance < 0:
            raise ValueError(f"tolerance must be >= 0, got {tolerance}")
        if max_iters < 1:
            raise ValueError(f"max_iters must be >= 1, got {max_iters}")
        self._jobs.append(_Job((tx, ty), side, tolerance, max_iters, expected=self._seen))

    def next_read(self) -> tuple[Rect, float] | None:
        """The region the next :meth:`step` reads and when the last move was.

        None when nothing is queued or in open loop (no read). Lets the
        capture fold the cursor ROI into the tick's single grab (P5).
        """
        if not self._jobs or self._config.open_loop:
            return None
        region = self._read_region(self._jobs[0])
        return None if region is None else (region, self._last_move)

    def cancel(self) -> None:
        """Drop every pending click (nothing more is sent for them)."""
        self._jobs.clear()

    def step(self) -> bool | None:
        """Advance the head click by one capture + one move (or the click).

        Returns:
            None while the click is pending (or nothing is queued), True
            when the click was sent this step, False when it failed (the
            whole queue is dropped -- fail closed, no blind click).
        """
        if not self._jobs:
            return None
        job = self._jobs[0]
        wait = self._config.settle_ms / 1000.0 - (self._clock() - self._last_move)
        if wait > 0:
            self._sleep(wait)
        pos = self._locate(job)
        if pos is None:
            if self._config.open_loop:
                return None  # still homing
            if job.expected is not None:
                job.expected = None  # missed the ROI: search the full region next step
                return None
            return self._fail(job, "cursor not found")
        self._seen = pos
        tx, ty = job.target
        dx, dy = tx - pos[0], ty - pos[1]
        dist = max(abs(dx), abs(dy))
        if job.budget is None:
            job.budget = math.ceil(dist / MAX_MOUSE_DELTA) + job.max_iters
        if dist <= job.tolerance:
            if not self._sent(lambda: self._input.click(job.side)):
                return None  # rate limited: click on the next step
            self._jobs.popleft()
            return True
        if job.budget <= 0:
            return self._fail(job, f"cursor not within +{job.tolerance} px")
        scale = self._config.mouse_scale if self._config.open_loop else 1.0
        sx = max(-MAX_MOUSE_DELTA, min(MAX_MOUSE_DELTA, round(dx / scale)))
        sy = max(-MAX_MOUSE_DELTA, min(MAX_MOUSE_DELTA, round(dy / scale)))
        if self._sent(lambda: self._input.mouse_move(sx, sy)):
            job.budget -= 1
            self._last_move = self._clock()
            job.expected = (pos[0] + round(sx * scale), pos[1] + round(sy * scale))
            if self._estimate is not None:
                self._estimate = (self._estimate[0] + sx * scale, self._estimate[1] + sy * scale)
        return None

    def click_at(
        self,
        target: tuple[int, int],
        *,
        tolerance: int = DEFAULT_TOLERANCE,
        max_iters: int = DEFAULT_MAX_ITERS,
        side: ClickSide = ClickSide.LEFT,
    ) -> bool:
        """Blocking click: :meth:`request` then :meth:`step` until done.

        For tools and tests only -- it blocks for several settle delays,
        so the game loop uses :meth:`request` / :meth:`step` instead.

        Returns:
            True when the cursor was verified within *tolerance* and the
            click was sent; False otherwise (no click is sent).
        """
        self.cancel()
        self.request(target, tolerance=tolerance, max_iters=max_iters, side=side)
        while True:
            result = self.step()
            if result is not None:
                return result

    # -- helpers ------------------------------------------------------------

    def _locate(self, job: _Job) -> tuple[int, int] | None:
        """Where the cursor is now: rendered (closed loop) or estimated."""
        if self._config.open_loop:
            if self._estimate is None:
                self._home()
                return None
            return (round(self._estimate[0]), round(self._estimate[1]))
        region = self._read_region(job)
        if region is None:
            return None
        frame = self._roi_provider(region) if self._roi_provider else self._frame_provider()
        return self._reader.read(frame, region)

    def _read_region(self, job: _Job) -> Rect | None:
        if job.expected is None:
            return self._reader.cursor_region  # first read: full search area
        h = self._config.roi_half
        return (job.expected[0] - h, job.expected[1] - h, 2 * h, 2 * h)

    def _home(self) -> None:
        """Open loop: one homing move per step; seed the estimate when done."""
        m = -MAX_MOUSE_DELTA
        if self._sent(lambda: self._input.mouse_move(m, m)):
            self._last_move = self._clock()
            self._homed += 1
        if self._homed >= self._config.home_moves:
            ox, oy = self._window_origin() if self._window_origin else (0, 0)
            self._estimate = (float(-ox), float(-oy))

    def _sent(self, send: Callable[[], None]) -> bool:
        """Run *send*; False when the wire rate limiter dropped it (F-20)."""
        before = getattr(self._input, "dropped", 0)
        send()
        return bool(getattr(self._input, "dropped", 0) == before)

    def _fail(self, job: _Job, why: str) -> bool:
        logger.debug("click at %s failed: %s", job.target, why)
        self._jobs.clear()
        return False

    @staticmethod
    def _as_point(target: object) -> tuple[int, int]:
        """Validate and coerce a click target to an ``(x, y)`` pair."""
        if not isinstance(target, Sequence) or isinstance(target, (str, bytes)):
            raise ValueError(f"target must be a (x, y) pair, got {target!r}")
        if len(target) != 2:
            raise ValueError(f"target must have 2 values, got {len(target)}")
        return (int(target[0]), int(target[1]))
