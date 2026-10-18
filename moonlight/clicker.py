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
intended target. So after every move, :meth:`ClickController.click_at`
re-reads the *rendered* cursor position with a
:class:`moonlight.perception.cursor.CursorReader` and issues correcting
moves until the verified position is within ``tolerance`` of the target.

Convergence contract
--------------------

* ``click_at(target)`` sends the click **exactly once, and only on
  convergence**: the last verified cursor position — read from the
  rendered cursor, never from the sent deltas — must satisfy
  ``max(|dx|, |dy|) <= tolerance`` (infinity norm) with respect to
  *target*. The default tolerance is ±2 pixels (T17).
* If the cursor cannot be verified within ``max_iters`` correcting moves,
  or is not found at all (``CursorReader.read`` returns ``None``),
  ``click_at`` returns ``False`` and sends **no click** — a missed click
  is safer than a blind click at an unverified position (fail closed).
* The initial positioning move is chunked so every wire ``M`` command
  stays within the protocol's -127..127 range (see
  :mod:`moonlight.input`); the correction loop then issues at most
  ``max_iters`` additional moves.

The controller takes a *frame provider* callable (in production it wraps
``moonlight.capture``; in tests it renders a synthetic cursor at the
:class:`moonlight.input.FakeInput` virtual cursor position) — no hard
dependency on the capture backend.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import numpy as np

from moonlight.input import MAX_MOUSE_DELTA, ClickSide, InputBackend
from moonlight.perception.cursor import CursorReader

__all__ = [
    "ClickController",
    "DEFAULT_TOLERANCE",
    "DEFAULT_MAX_ITERS",
]

logger = logging.getLogger("moonlight.clicker")

#: Default convergence tolerance in pixels (±2 px, T17 requirement).
DEFAULT_TOLERANCE = 2

#: Default maximum number of correcting moves after the initial move.
DEFAULT_MAX_ITERS = 5


class ClickController:
    """Closed-loop click: move -> verify rendered cursor -> correct -> click.

    Example:
        >>> reader = CursorReader(
        ...     cursor_region=(0, 0, 800, 600),
        ...     cursor_templates=[cursor_frame],
        ... )
        >>> controller = ClickController(
        ...     input_backend, reader, frame_provider=capture.get_frame
        ... )
        >>> if controller.click_at((412, 233)):
        ...     logger.info("clicked at (412, 233)")
    """

    def __init__(
        self,
        input_backend: InputBackend,
        cursor_reader: CursorReader,
        frame_provider: Callable[[], np.ndarray],
    ) -> None:
        """Create a controller.

        Args:
            input_backend: The :class:`moonlight.input.InputBackend` that
                sends moves and clicks (``ArduinoInput`` in production,
                ``FakeInput`` in tests).
            cursor_reader: Reads the rendered cursor position off frames.
            frame_provider: Callable returning the current frame (RGB
                ``(H, W, 3)`` ``uint8``). Called once before the first
                move and once before every correction.
        """
        self._input = input_backend
        self._reader = cursor_reader
        self._frame_provider = frame_provider

    # -- public API ---------------------------------------------------------

    def click_at(
        self,
        target: tuple[int, int],
        *,
        tolerance: int = DEFAULT_TOLERANCE,
        max_iters: int = DEFAULT_MAX_ITERS,
        side: ClickSide = ClickSide.LEFT,
    ) -> bool:
        """Click *target* once the cursor is verified within *tolerance*.

        Sequence: read the actual cursor position, move toward the target
        (chunked to the wire protocol's per-command range), then verify
        and correct up to *max_iters* times; the click is sent exactly
        once, on convergence.

        Args:
            target: ``(x, y)`` click target in frame (screen-window)
                coordinates.
            tolerance: Maximum allowed distance (infinity norm, pixels)
                between the verified cursor position and *target* before
                clicking.
            max_iters: Maximum number of correcting moves after the
                initial move.
            side: Click button (default left).

        Returns:
            True when the cursor was verified within tolerance and the
            click was sent. False when it could not be verified (no
            click is sent — see the module docstring for the contract).

        Raises:
            ValueError: If *target* is not a 2-element pair, or
                *tolerance*/*max_iters* are out of range.
        """
        tx, ty = self._as_point(target)
        if tolerance < 0:
            raise ValueError(f"tolerance must be >= 0, got {tolerance}")
        if max_iters < 1:
            raise ValueError(f"max_iters must be >= 1, got {max_iters}")

        # 1. Where is the cursor *actually* right now?
        start = self._reader.read(self._frame_provider())
        if start is None:
            logger.debug(
                "click_at: cursor not found; skipping click at (%d, %d)", tx, ty
            )
            return False

        # 2. Move toward the target (chunked to fit the wire protocol).
        self._send_move(tx - start[0], ty - start[1])

        # 3. Verify and correct until within tolerance (or max_iters).
        for _ in range(max_iters):
            pos = self._reader.read(self._frame_provider())
            if pos is None:
                # Cannot verify: no correction info; try again next round.
                continue
            dx, dy = tx - pos[0], ty - pos[1]
            if max(abs(dx), abs(dy)) <= tolerance:
                self._input.click(side)
                return True
            self._send_move(dx, dy)

        logger.debug(
            "click_at: cursor not within +%d px of (%d, %d) after %d moves",
            tolerance,
            tx,
            ty,
            max_iters,
        )
        return False

    # -- helpers ------------------------------------------------------------

    def _send_move(self, dx: int, dy: int) -> None:
        """Send ``(dx, dy)`` as one or more M commands within ±127/axis."""
        while dx or dy:
            sx = max(-MAX_MOUSE_DELTA, min(MAX_MOUSE_DELTA, dx))
            sy = max(-MAX_MOUSE_DELTA, min(MAX_MOUSE_DELTA, dy))
            self._input.mouse_move(sx, sy)
            dx -= sx
            dy -= sy

    @staticmethod
    def _as_point(target: object) -> tuple[int, int]:
        """Validate and coerce a click target to an ``(x, y)`` pair."""
        if not isinstance(target, Sequence) or isinstance(target, (str, bytes)):
            raise ValueError(f"target must be a (x, y) pair, got {target!r}")
        if len(target) != 2:
            raise ValueError(f"target must have 2 values, got {len(target)}")
        return (int(target[0]), int(target[1]))
