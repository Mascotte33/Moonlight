"""Live state bridge for the GUI (T39).

Connects the GUI to the real bot runtime without touching bot logic:

* :class:`LogFeed` — thread-safe ring buffer of log lines (the GUI's event
  log; mirrors ``ui.py``'s terminal log).
* :class:`BotController` — runs :class:`moonlight.loop.GameLoop` on a daemon
  thread with start/stop/kill semantics (the loop's own F12 kill-switch and
  stop-event do the real work; the controller just hosts the thread).
* :class:`StatePoller` — polls the shared :class:`moonlight.state.WorldState`
  on a Tk timer and pushes a snapshot into the tabs. Polling is a fast
  ``snapshot()`` call on the Tk thread; the loop thread is never blocked.
* :class:`HardwareController` — owns the :class:`moonlight.input.ArduinoInput`
  (or FakeInput) connection: port listing, connect/ping/release_all/close.
* :class:`GuiContext` — the single object handed to every tab.

.. note::
   The GUI's "Start bot" does **not** run perception in a side thread. It
   builds the real bot loop through :func:`moonlight.wiring.build_loop` (see
   :mod:`moonlight.gui.app`), which wires a real screen capture, the focus
   gate and every enabled decision module. The previous ``PerceptionWriter``
   background thread was removed (F-13): it duplicated ``make_perceive`` and
   hard-coded ``client_focused = True``, which would have bypassed the
   safety focus gate.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from moonlight.gui.config_bridge import BotConfig, ConfigStore
from moonlight.state import WorldState

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.input import InputBackend
    from moonlight.loop import GameLoop

__all__ = [
    "BotController",
    "GuiContext",
    "HardwareController",
    "LogFeed",
    "StatePoller",
]


# ---------------------------------------------------------------------------
# Log feed
# ---------------------------------------------------------------------------


class LogFeed:
    """Thread-safe ring buffer of ``(timestamp, level, message)`` lines.

    The loop thread, hardware thread and Tk thread all write here; the
    Dashboard/Diagnostics tabs render the tail on poll.
    """

    def __init__(self, maxlen: int = 200) -> None:
        self._lock = threading.Lock()
        self._lines: deque[tuple[float, str, str]] = deque(maxlen=maxlen)

    def add(self, level: str, message: str) -> None:
        """Append one line (thread-safe)."""
        with self._lock:
            self._lines.append((time.time(), level, message))

    def info(self, message: str) -> None:
        """Append an informational line."""
        self.add("info", message)

    def warn(self, message: str) -> None:
        """Append a warning line."""
        self.add("warn", message)

    def error(self, message: str) -> None:
        """Append an error line."""
        self.add("error", message)

    def recent(self, n: int = 30) -> list[tuple[float, str, str]]:
        """The last *n* lines, oldest first (thread-safe copy)."""
        with self._lock:
            items = list(self._lines)
        return items[-n:]

    def text(self, n: int = 30) -> str:
        """The last *n* lines as ``[HH:MM:SS] LEVEL message`` text."""
        out: list[str] = []
        for ts, level, msg in self.recent(n):
            out.append(time.strftime("%H:%M:%S", time.localtime(ts)) + f" [{level}] {msg}")
        return "\n".join(out)

    def errors(self, n: int = 10) -> list[tuple[float, str, str]]:
        """The last *n* lines with level ``error`` (thread-safe copy)."""
        with self._lock:
            items = [ln for ln in self._lines if ln[1] == "error"]
        return items[-n:]


# ---------------------------------------------------------------------------
# Bot loop controller
# ---------------------------------------------------------------------------


class BotController:
    """Hosts the :class:`~moonlight.loop.GameLoop` on a daemon thread.

    Start/stop/kill delegate to the loop's own thread-safe controls
    (``start`` runs ``loop.run()`` in the thread; ``stop``/``kill`` set the
    loop's events and join the thread with a timeout so the GUI never hangs).
    """

    def __init__(
        self, loop: GameLoop, join_timeout: float = 5.0, log: LogFeed | None = None
    ) -> None:
        self.loop = loop
        self.join_timeout = join_timeout
        self.log = log
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None

    @property
    def is_running(self) -> bool:
        """True while the loop thread is alive."""
        t = self._thread
        return t is not None and t.is_alive()

    @property
    def state(self) -> Any:
        """The loop's current :class:`~moonlight.loop.BotState`."""
        return self.loop.current_state

    def start(self) -> None:
        """Start the loop thread. Raises ``RuntimeError`` if already running."""
        with self._lock:
            if self.is_running:
                raise RuntimeError("bot loop already running")
            self.last_error = None
            thread = threading.Thread(
                target=self._run, name="moonlight-gui-bot", daemon=True
            )
            self._thread = thread
        thread.start()
        if self.log is not None:
            self.log.info("bot loop started (GUI)")

    def _run(self) -> None:
        try:
            self.loop.run()
        except Exception as exc:  # noqa: BLE001 - report, don't kill the app
            self.last_error = repr(exc)
            if self.log is not None:
                self.log.error(f"bot loop thread died: {exc!r}")

    def stop(self, reason: str = "GUI stop") -> None:
        """Request a clean stop and join the thread (bounded)."""
        self.loop.stop(reason)
        self._join()
        if self.log is not None:
            self.log.info(f"bot loop stopped ({reason})")

    def kill(self) -> None:
        """Activate the kill switch and join the thread (bounded)."""
        self.loop.kill()
        self._join()
        if self.log is not None:
            self.log.warn("kill switch activated (GUI)")

    def _join(self) -> None:
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=self.join_timeout)
        with self._lock:
            if self._thread is thread and thread is not None and not thread.is_alive():
                self._thread = None


# ---------------------------------------------------------------------------
# State poller
# ---------------------------------------------------------------------------


class _TkLike(Protocol):
    """The subset of Tk behavior the poller needs (testable with fakes)."""

    def after(self, ms: int, callback: Callable[[], None]) -> object: ...

    def after_cancel(self, id: object) -> None: ...


class StatePoller:
    """Polls a :class:`WorldState` on a Tk timer and pushes snapshots.

    The poll runs ``state.snapshot()`` — a fast, lock-guarded read — on the
    Tk thread, so the bot loop thread is never blocked by the GUI. *on_poll*
    receives each snapshot; exceptions in it are reported via *on_error* and
    never kill the timer.
    """

    def __init__(
        self,
        master: _TkLike,
        state: WorldState,
        interval_ms: int = 100,
        on_poll: Callable[[WorldState], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._master = master
        self._state = state
        self._interval = max(10, int(interval_ms))
        self._on_poll = on_poll
        self._on_error = on_error
        self._after_id: object | None = None
        self._stopped = True
        self.poll_count = 0

    @property
    def running(self) -> bool:
        """True while the timer is scheduled."""
        return not self._stopped

    def start(self) -> None:
        """Arm the timer (idempotent)."""
        self._stopped = False
        if self._after_id is None:
            self._after_id = self._master.after(self._interval, self._tick)

    def stop(self) -> None:
        """Cancel the timer (idempotent)."""
        self._stopped = True
        if self._after_id is not None:
            self._master.after_cancel(self._after_id)
            self._after_id = None

    def poll_once(self) -> WorldState:
        """Take one snapshot and dispatch it (used by tests and the tick)."""
        snap = self._state.snapshot()
        self.poll_count += 1
        if self._on_poll is not None:
            self._on_poll(snap)
        return snap

    def _tick(self) -> None:
        self._after_id = None
        if self._stopped:
            return
        try:
            self.poll_once()
        except Exception as exc:  # noqa: BLE001 - a bad tab must not kill the timer
            if self._on_error is not None:
                self._on_error(f"state poll failed: {exc!r}")
        if not self._stopped:
            self._after_id = self._master.after(self._interval, self._tick)


# ---------------------------------------------------------------------------
# Hardware controller
# ---------------------------------------------------------------------------


class HardwareController:
    """Owns the Arduino connection for the Hardware tab.

    Port listing uses ``serial.tools.list_ports``; connect builds an
    :class:`moonlight.input.ArduinoInput` (blocking — call from a worker
    thread); ping/release_all/close delegate to the backend. The factory is
    injectable so tests run without hardware.
    """

    def __init__(
        self,
        input_factory: Callable[..., InputBackend] | None = None,
        log: LogFeed | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._backend: InputBackend | None = None
        self._factory = input_factory
        self.log = log
        self.last_error: str | None = None

    @property
    def backend(self) -> InputBackend | None:
        """The connected backend, or None."""
        with self._lock:
            return self._backend

    @property
    def port(self) -> str | None:
        """The connected port (None when disconnected)."""
        backend = self.backend
        if backend is None:
            return None
        getter = getattr(backend, "connected_port", None)
        return getter() if callable(getter) else None

    def list_ports(self) -> list[dict[str, str]]:
        """List available serial ports as dicts (device, description, hwid).

        Returns an empty list when pyserial is unavailable (it is a hard
        dependency in practice; the guard keeps display-only mode easy).
        """
        try:
            from serial.tools import list_ports  # type: ignore[import-untyped]
        except ImportError:  # pragma: no cover - pyserial is a dependency
            return []
        out: list[dict[str, str]] = []
        for info in list_ports.comports():
            out.append(
                {
                    "device": info.device,
                    "description": info.description or "",
                    "hwid": info.hwid or "",
                }
            )
        return out

    def connect(self, port: str | None = None, baudrate: int = 115200) -> InputBackend:
        """Open the Arduino on *port* (auto-detect when None).

        Closes any previous connection first. Returns the backend.
        Raises on failure (callers surface the message in the tab).
        """
        if self._factory is None:
            from moonlight.input import ArduinoInput

            self._factory = lambda p, b: ArduinoInput(  # noqa: E731
                port=p, baudrate=b, heartbeat_interval=1.0
            )
        with self._lock:
            if self._backend is not None:
                self._close_locked()
            self.last_error = None
            try:
                self._backend = self._factory(port, baudrate)
            except Exception as exc:  # noqa: BLE001 - report to the tab
                self.last_error = repr(exc)
                self._backend = None
                raise
            backend = self._backend
        name = getattr(backend, "connected_port", None)
        label = name() if callable(name) else repr(backend)
        if self.log is not None:
            self.log.info(f"arduino connected: {label}")
        return backend

    def _close_locked(self) -> None:
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception:  # noqa: BLE001 - closing best-effort
                pass
            self._backend = None

    def ping(self) -> bool | None:
        """Ping the backend (PONG = True); None when disconnected."""
        with self._lock:
            backend = self._backend
        if backend is None:
            return None
        try:
            return bool(backend.ping())
        except Exception as exc:  # noqa: BLE001 - surface to the tab
            self.last_error = repr(exc)
            raise

    def release_all(self) -> None:
        """Release all held inputs on the backend (no-op when disconnected)."""
        with self._lock:
            backend = self._backend
        if backend is not None:
            backend.release_all()

    def disconnect(self) -> None:
        """Close the backend (idempotent)."""
        with self._lock:
            self._close_locked()
            if self.log is not None:
                self.log.info("arduino disconnected")


# ---------------------------------------------------------------------------
# Context handed to every tab
# ---------------------------------------------------------------------------


@dataclass
class GuiContext:
    """The shared dependencies every tab receives.

    Tabs receive this in their constructor (dependency injection) so they can
    be built and tested without a display: they read ``ctx.state`` snapshots
    in :meth:`on_poll` and mutate config through ``ctx.config_store``.
    """

    state: WorldState
    config_store: ConfigStore
    hardware: HardwareController
    log: LogFeed
    controller: BotController | None = None
    profile_name: str | None = None

    def active_config(self) -> BotConfig:
        """A fresh copy of the live config (thread-safe)."""
        return self.config_store.snapshot()

    def bot_running(self) -> bool:
        """True while the bot loop thread is alive (False in --no-bot)."""
        return self.controller is not None and self.controller.is_running
