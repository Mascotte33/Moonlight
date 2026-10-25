"""Safety primitives for the game loop: wire rate limit, F12 kill switch, alerts.

:class:`RateLimitedInput` caps the serial commands that reach the device
(F-20); :class:`F12Monitor` polls the keyboard and fires the kill switch;
:func:`beep_alert` is the operator alert sink. :class:`moonlight.bot.loop.GameLoop`
wires all three.
"""

from __future__ import annotations

import ctypes
import logging
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from moonlight import x11
from moonlight.hardware.input import ClickSide, InputBackend

__all__ = [
    "VK_F12",
    "F12Monitor",
    "RateLimitedInput",
    "RateLimiter",
    "beep_alert",
    "default_key_state",
]

logger = logging.getLogger("moonlight.bot.safety")

#: Win32 virtual-key code for F12.
VK_F12 = 0x7B
_LINUX_BELL = "/usr/share/sounds/freedesktop/stereo/bell.oga"


def default_key_state(vk: int) -> bool:
    """Return True if virtual key *vk* is currently held down.

    Uses Win32 ``GetAsyncKeyState`` polling on Windows and the X11 key map
    on Linux (function keys only); always False elsewhere (the programmatic
    kill switch still works everywhere).
    """
    if sys.platform == "linux":
        return x11.vk_down(vk)
    if sys.platform != "win32":
        return False
    state = ctypes.windll.user32.GetAsyncKeyState(vk)
    return bool(state & 0x8000)  # "currently down"; 0x0001 is unreliable


def beep_alert(message: str) -> None:
    """Operator alert sink: a non-blocking system beep. Never raises.

    The loop logs every alert itself; this only adds the sound. Linux plays
    the desktop bell sound through ``paplay`` when both exist.
    """
    if sys.platform == "linux":
        if shutil.which("paplay") and Path(_LINUX_BELL).exists():
            try:
                subprocess.Popen(["paplay", _LINUX_BELL], stderr=subprocess.DEVNULL)
            except OSError:
                logger.debug("alert sound failed", exc_info=True)
        return
    if sys.platform != "win32":
        return
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:  # noqa: BLE001 - an alert must never take the loop down
        logger.debug("alert beep failed", exc_info=True)


class RateLimiter:
    """Token-bucket rate limiter.

    Refills at *rate* tokens per second up to *capacity* tokens.
    ``try_acquire`` never blocks: it returns ``False`` when not enough
    tokens are available. A monotonic clock may be injected for tests.
    """

    def __init__(
        self,
        rate: float,
        capacity: float | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self._rate = float(rate)
        self._capacity = float(rate if capacity is None else capacity)
        if self._capacity <= 0:
            raise ValueError("capacity must be > 0")
        self._clock = clock if clock is not None else time.monotonic
        self._tokens = self._capacity
        self._last_refill = self._clock()

    @property
    def rate(self) -> float:
        """Tokens per second."""
        return self._rate

    @property
    def capacity(self) -> float:
        """Maximum stored tokens."""
        return self._capacity

    def try_acquire(self, tokens: float = 1.0, reserve: float = 0.0) -> bool:
        """Consume *tokens* if available, leaving *reserve* behind; never blocks."""
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now
        if tokens + reserve > self._tokens:
            return False
        self._tokens -= tokens
        return True

    def reset(self) -> None:
        """Refill the bucket (e.g. between test scenarios)."""
        self._tokens = self._capacity
        self._last_refill = self._clock()


class RateLimitedInput(InputBackend):
    """Wire-level rate limit: every serial command pays one token (F-20).

    Wraps the real backend so the 10/s invariant holds for what reaches
    the device, not for "actions" (one clicker or tile action is several
    commands). ``press_combo``/``hold_combo``/``mouse_move``/``click``
    leave :attr:`reserve` tokens behind and are **dropped** (never
    blocked, so a tick never stalls) when the bucket is empty. Releases,
    ``ping`` and ``close`` always pass: a stop must never be rate-limited.
    The Arduino heartbeat (``P``) is internal to the backend and not an
    input. :attr:`dropped` counts dropped commands.
    """

    def __init__(self, backend: InputBackend, limiter: RateLimiter) -> None:
        self.backend = backend
        self.limiter = limiter
        #: Tokens to leave in the bucket (the loop sets 0 for heals).
        self.reserve = 1.0
        self.dropped = 0

    def _pay(self, op: str) -> bool:
        if self.limiter.try_acquire(1.0, reserve=self.reserve):
            return True
        self.dropped += 1
        logger.debug("wire rate limit: dropped %s", op)
        return False

    def press_combo(self, combo: str) -> None:
        if self._pay(f"press_combo {combo}"):
            logger.debug("key %s", combo)
            self.backend.press_combo(combo)

    def hold_combo(self, combo: str) -> None:
        if self._pay("hold_combo"):
            self.backend.hold_combo(combo)

    def release_combo(self, combo: str) -> None:
        self.backend.release_combo(combo)

    def mouse_move(self, dx: int, dy: int) -> None:
        if self._pay("mouse_move"):
            self.backend.mouse_move(dx, dy)

    def mouse_to(self, x: int, y: int) -> None:
        if self._pay("mouse_to"):
            self.backend.mouse_to(x, y)

    def click(self, side: ClickSide) -> None:
        if self._pay("click"):
            self.backend.click(side)

    def ping(self) -> bool:
        return self.backend.ping()

    def release_all(self) -> None:
        self.backend.release_all()

    def close(self) -> None:
        self.backend.close()

    @classmethod
    def wrap(
        cls,
        backend: InputBackend,
        rate: float,
        clock: Callable[[], float] | None = None,
    ) -> RateLimitedInput:
        """*backend* behind a fresh wire limiter (capacity 2: one heal token)."""
        if isinstance(backend, cls):
            return backend
        return cls(backend, RateLimiter(rate=rate, capacity=2.0, clock=clock))


class F12Monitor:
    """Daemon thread that polls F12 and fires ``on_press`` on a rising edge.

    Uses keyboard *polling* (GetAsyncKeyState, or the X11 key map) rather than a low-level
    hook: simpler, and it cannot be blocked by hooks installed by other
    applications. ``on_press`` runs in the monitor thread, so it must be
    thread-safe and fast (the loop only sets an event). The key-state
    probe is injectable for tests.
    """

    def __init__(
        self,
        on_press: Callable[[], None],
        poll_interval: float = 0.01,
        key_state: Callable[[int], bool] | None = None,
        vk: int = VK_F12,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be > 0")
        self._on_press = on_press
        self._poll_interval = poll_interval
        self._key_state = key_state if key_state is not None else default_key_state
        self._vk = vk
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        """True while the monitor thread is alive."""
        t = self._thread
        return t is not None and t.is_alive()

    def start(self) -> None:
        """Start the monitor thread (no-op if already running)."""
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="moonlight-f12", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the monitor thread and wait (bounded) for it to exit."""
        self._stop_event.set()
        t = self._thread
        self._thread = None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=max(0.2, 5.0 * self._poll_interval))

    def _run(self) -> None:
        prev_down = False
        while not self._stop_event.wait(self._poll_interval):
            down = self._key_state(self._vk)
            if down and not prev_down:
                logger.info("F12 kill switch pressed")
                try:
                    self._on_press()
                except Exception:
                    logger.exception("F12 on_press callback failed")
            prev_down = down
