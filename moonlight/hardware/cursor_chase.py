"""Absolute mouse moves on Linux: relative steps checked against the real cursor.

The firmware's absolute pointer is a Generic Desktop / Mouse collection like
the stock relative mouse, so Linux merges both into one "Arduino Leonardo
Mouse" device and libinput ignores the absolute axes of a relative mouse:
``A x y`` never moves the cursor. Pointer acceleration makes one relative
step land anywhere from 1x to 2x (measured 100 px -> 105..183 px), so each
step goes half the way until the cursor is a few px off (1..3 steps).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from moonlight.hardware.input import ABS_MAX, ClickSide, InputBackend

__all__ = ["CursorChaseInput"]

logger = logging.getLogger(__name__)

#: Seconds for X to show a relative step before the cursor is read again.
SETTLE = 0.03
#: Steps before giving up on a spot.
TRIES = 8
#: px the cursor may stay off the spot.
TOLERANCE = 1
#: Below this many px a step goes the whole way (acceleration is ~1x there).
FULL_STEP = 6


def _step(distance: int) -> int:
    return distance if abs(distance) <= FULL_STEP else int(distance * 0.5)


class CursorChaseInput(InputBackend):
    """*inner* with ``mouse_to`` done as relative steps towards the spot."""

    def __init__(
        self,
        inner: InputBackend,
        cursor: Callable[[], tuple[int, int] | None],
        screen: tuple[int, int, int, int],
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.inner = inner
        self._cursor = cursor
        self._screen = screen
        self._sleep = sleep

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def mouse_to(self, x: int, y: int) -> None:
        """Move the cursor to the absolute spot (each axis 0..ABS_MAX)."""
        left, top, w, h = self._screen
        tx = left + x * w // (ABS_MAX + 1)
        ty = top + y * h // (ABS_MAX + 1)
        for _ in range(TRIES):
            at = self._cursor()
            if at is None:
                raise RuntimeError("cursor position unknown: cannot move the mouse")
            dx, dy = tx - at[0], ty - at[1]
            if abs(dx) <= TOLERANCE and abs(dy) <= TOLERANCE:
                return
            self.inner.mouse_move(_step(dx), _step(dy))
            self._sleep(SETTLE)
        logger.warning("mouse: still %d,%d px off %d,%d after %d steps", dx, dy, tx, ty, TRIES)

    def press_combo(self, combo: str) -> None:
        self.inner.press_combo(combo)

    def hold_combo(self, combo: str) -> None:
        self.inner.hold_combo(combo)

    def release_combo(self, combo: str) -> None:
        self.inner.release_combo(combo)

    def mouse_move(self, dx: int, dy: int) -> None:
        self.inner.mouse_move(dx, dy)

    def click(self, side: ClickSide) -> None:
        self.inner.click(side)

    def mouse_down(self, side: ClickSide) -> None:
        self.inner.mouse_down(side)

    def mouse_up(self, side: ClickSide) -> None:
        self.inner.mouse_up(side)

    def ping(self) -> bool:
        return self.inner.ping()

    def release_all(self) -> None:
        self.inner.release_all()

    def close(self) -> None:
        self.inner.close()
