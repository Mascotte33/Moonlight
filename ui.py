"""Terminal UI for MOONLIGHT (T27).

A lightweight control interface for the bot, built on the standard library
only — no GUI toolkit, no new dependencies. It renders to the terminal with
optional ANSI colors (auto-disabled when stdout is not a TTY or ``NO_COLOR``
is set).

Components:
    BotController:
        Runs a :class:`moonlight.loop.GameLoop` on a daemon thread;
        start / stop / kill / is_running.
    ProfileSelector:
        Lists and loads calibration profiles from ``profiles/<name>.json``.
    StateDisplay:
        Renders a :class:`moonlight.state.WorldState` snapshot as a text block.
    LogViewer:
        Thread-safe ring buffer of recent log messages.
    LogViewerHandler:
        A ``logging.Handler`` that forwards records (bot decisions/actions
        from ``moonlight.loop`` & co.) into a :class:`LogViewer`.
    MoonlightUI:
        The interactive main loop: displays state + logs, handles commands.

Commands (at the ``moonlight>`` prompt):
    start            start the bot loop
    stop             stop the bot loop cleanly
    kill             emergency kill switch (release all inputs, stop)
    profiles         list available calibration profiles
    profile <name>   load a calibration profile
    state            redraw the world state
    log [n]          show the last n log lines (default 10)
    stats            show bot loop statistics
    help             show help
    quit | exit | q  leave

Wiring note: :func:`main` is the entry point — run it with the package
importable (e.g. under pytest's conftest path or ``PYTHONPATH=.``):
``python -m moonlight.ui``. Until T16 provides ``ArduinoInput``, it wires
the loop with :class:`moonlight.input.FakeInput` as a placeholder backend
so the UI can be developed and tested offline.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from moonlight.calibrate import (
    CalibrationApp,
    CalibrationError,
    CalibrationProfile,
    default_profiles_dir,
    load_image_bgr,
)
from moonlight.input import FakeInput
from moonlight.loop import GameLoop
from moonlight.state import Monster, Position, WorldState

__all__ = [
    "BotController",
    "LogViewer",
    "LogViewerHandler",
    "MoonlightUI",
    "ProfileSelector",
    "StateDisplay",
    "main",
]

logger = logging.getLogger("moonlight.ui")

#: ANSI escape sequences (only emitted when color is enabled).
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"


# ---------------------------------------------------------------------------
# LogViewer
# ---------------------------------------------------------------------------


class LogViewer:
    """Thread-safe ring buffer of recent log messages."""

    def __init__(self, maxlen: int = 200) -> None:
        if maxlen <= 0:
            raise ValueError("maxlen must be > 0")
        self._maxlen = maxlen
        self._messages: deque[str] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add_log(self, msg: str) -> None:
        """Append one message (oldest messages are evicted at ``maxlen``)."""
        with self._lock:
            self._messages.append(msg)

    def get_recent(self, n: int) -> list[str]:
        """Return the most recent *n* messages, oldest first."""
        if n < 0:
            raise ValueError("n must be >= 0")
        with self._lock:
            items = list(self._messages)
        return items[-n:] if n else []

    def clear(self) -> None:
        """Drop all stored messages."""
        with self._lock:
            self._messages.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._messages)

    def __contains__(self, msg: str) -> bool:
        with self._lock:
            return msg in self._messages


class LogViewerHandler(logging.Handler):
    """A ``logging.Handler`` that forwards records into a :class:`LogViewer`.

    Attach it to the ``moonlight`` logger to see bot decisions/actions
    (state transitions, kill switch, etc.) in the UI log view.
    """

    def __init__(
        self,
        viewer: LogViewer,
        *,
        formatter: logging.Formatter | None = None,
    ) -> None:
        super().__init__()
        self._viewer = viewer
        if formatter is None:
            formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
        self.setFormatter(formatter)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # noqa: BLE001 - a handler must never raise into logging
            self.handleError(record)
            return
        self._viewer.add_log(msg)


# ---------------------------------------------------------------------------
# ProfileSelector
# ---------------------------------------------------------------------------


class ProfileSelector:
    """Lists and loads calibration profiles from ``profiles/<name>.json``."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self._dir = Path(directory) if directory is not None else default_profiles_dir()

    @property
    def directory(self) -> Path:
        """The profiles directory this selector reads from."""
        return self._dir

    def list_profiles(self) -> list[str]:
        """Return profile names (file stems) in the profiles directory."""
        if not self._dir.is_dir():
            return []
        return sorted(p.stem for p in self._dir.glob("*.json"))

    def select_profile(self, name: str) -> CalibrationProfile:
        """Load and validate the profile *name*.

        Raises:
            ValueError: If the profile does not exist.
            CalibrationError: If the profile file is malformed or incomplete.
        """
        available = self.list_profiles()
        if name not in available:
            listing = ", ".join(available) if available else "(none)"
            raise ValueError(f"Unknown profile {name!r}. Available: {listing}")
        profile = CalibrationProfile.load(self._dir / f"{name}.json")
        profile.validate()
        return profile


# ---------------------------------------------------------------------------
# StateDisplay
# ---------------------------------------------------------------------------


class StateDisplay:
    """Renders a :class:`WorldState` snapshot as a readable text block.

    Callers pass the live ``WorldState``; :meth:`render` always works on
    ``state.snapshot()`` per the reader/writer contract in ``state.py``.
    """

    BAR_WIDTH: int = 20

    def __init__(self, color: bool = False) -> None:
        self.color = color

    def render(self, state: WorldState, bot_state: str | None = None) -> str:
        """Return the display text for one state snapshot.

        Args:
            state: The live world state (snapshotted internally).
            bot_state: Optional current bot state name (e.g. ``"attack"``).
        """
        s = state.snapshot()
        lines: list[str] = []

        title = "MOONLIGHT"
        if bot_state is not None:
            title += f"  [{bot_state}]"
        lines.append(self._paint(f"  {title}", BOLD))

        lines.append(
            f"  focus: {self._yn(s.client_focused)}  "
            f"screen: {self._ok(s.screen_ok)}  "
            f"players: {s.players_on_screen}"
        )
        lines.append(self._paint(f"  HP {self._bar(s.hp_percent)}", self._hp_color(s.hp_percent)))
        lines.append(self._paint(f"  MP {self._bar(s.mp_percent)}", CYAN))
        cap = s.capacity
        lines.append(
            f"  pos: {self._pos(s.position)}  "
            f"cap: {cap.percent:.0f}% ({cap.weight:.0f}/{cap.max_weight:.0f})"
        )
        if s.selected_target is not None:
            t = s.selected_target
            lines.append(f"  target: {t.name} ({t.hp_percent:.0f}%)")
        if s.monsters:
            monsters = ", ".join(self._monster_str(m) for m in s.monsters)
            lines.append(f"  monsters ({len(s.monsters)}): {monsters}")
        if s.buffs:
            buffs = ", ".join(f"{name} {secs:.1f}s" for name, secs in sorted(s.buffs.items()))
            lines.append(f"  buffs: {buffs}")
        return "\n".join(lines)

    # -- helpers -------------------------------------------------------------

    def _paint(self, text: str, code: str) -> str:
        if not self.color:
            return text
        return f"{code}{text}{RESET}"

    def _bar(self, percent: float) -> str:
        pct = percent if 0.0 <= percent <= 100.0 else 0.0
        width = self.BAR_WIDTH
        filled = max(0, min(width, round(width * pct / 100.0)))
        bar = "#" * filled + "-" * (width - filled)
        return f"[{bar}] {pct:4.1f}%"

    def _pos(self, p: Position) -> str:
        if (p.x, p.y, p.z) == (0, 0, 0):
            return "?"
        return f"{p.x}/{p.y}/{p.z}"

    @staticmethod
    def _monster_str(m: Monster) -> str:
        suffix = " (player)" if m.is_player else ""
        return f"{m.name} {m.hp_percent:.0f}%{suffix}"

    @staticmethod
    def _yn(flag: bool) -> str:
        return "yes" if flag else "no"

    @staticmethod
    def _ok(flag: bool) -> str:
        return "ok" if flag else "BAD"

    def _hp_color(self, hp_percent: float) -> str:
        if hp_percent <= 0.0:
            return DIM
        if hp_percent >= 60.0:
            return GREEN
        if hp_percent >= 30.0:
            return YELLOW
        return RED


# ---------------------------------------------------------------------------
# BotController
# ---------------------------------------------------------------------------

LoopFactory = Callable[[], GameLoop]


class BotController:
    """Starts/stops a :class:`GameLoop` on a daemon thread.

    ``GameLoop.run()`` blocks, so the controller runs it on its own thread.
    A :class:`GameLoop` can only run once (kill/stop events persist), so an
    optional *loop_factory* supplies a fresh loop when a restart is
    requested after the previous loop is exhausted.
    """

    def __init__(self, loop: GameLoop, *, loop_factory: LoopFactory | None = None) -> None:
        self._loop = loop
        self._loop_factory = loop_factory
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def loop(self) -> GameLoop:
        """The GameLoop this controller is managing."""
        return self._loop

    def is_running(self) -> bool:
        """True while the bot loop thread is alive."""
        t = self._thread
        return t is not None and t.is_alive()

    def start(self) -> None:
        """Start the bot loop thread (no-op if already running).

        Raises:
            RuntimeError: If the current loop is exhausted (kill/stop
                already active) and no ``loop_factory`` was provided.
        """
        with self._lock:
            if self.is_running():
                return
            if self._loop.kill_switch_active or self._loop.stop_requested:
                if self._loop_factory is None:
                    raise RuntimeError(
                        "loop is exhausted (kill/stop already active); "
                        "no loop_factory was provided for a restart"
                    )
                logger.info("loop exhausted; building a fresh loop")
                self._loop = self._loop_factory()
            self._thread = threading.Thread(
                target=self._loop.run,
                name="moonlight-bot",
                daemon=True,
            )
            self._thread.start()

    def stop(self, reason: str = "ui: stop") -> None:
        """Request a clean stop and wait for the loop thread to exit."""
        self._loop.stop(reason)
        self.wait(2.0)

    def kill(self) -> None:
        """Activate the kill switch (release all inputs, then stop)."""
        self._loop.kill()
        self.wait(2.0)

    def wait(self, timeout: float | None = None) -> bool:
        """Join the bot thread; True if it has exited (or was never started)."""
        t = self._thread
        if t is None:
            return True
        if t is not threading.current_thread():
            t.join(timeout)
        return not t.is_alive()


# ---------------------------------------------------------------------------
# MoonlightUI
# ---------------------------------------------------------------------------

_HELP_TEXT = (
    "commands:\n"
    "  start            start the bot loop\n"
    "  stop             stop the bot loop cleanly\n"
    "  kill             emergency kill switch (release all inputs, stop)\n"
    "  profiles         list available calibration profiles\n"
    "  profile <name>   load a calibration profile\n"
    "  calibrate [name] open the calibration window (draw regions on screen)\n"
    "                   calibrate <name> --screenshot <path>  use a saved image\n"
    "  state            redraw the world state\n"
    "  log [n]          show the last n log lines (default 10)\n"
    "  stats            show bot loop statistics\n"
    "  help             show this help\n"
    "  quit | exit | q  leave"
)


def _default_color() -> bool:
    """True when ANSI colors are appropriate for this terminal."""
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _read_line_default() -> str | None:
    """Blocking stdin read; None on EOF."""
    try:
        return input()
    except EOFError:
        return None


def _print_default(text: str) -> None:
    print(text)


class MoonlightUI:
    """The interactive main loop: displays state and handles user input.

    See the module docstring for the command reference. All I/O is
    injectable (``input_fn`` / ``output`` on :meth:`run`), so the UI is
    fully testable offline.
    """

    PROMPT = "moonlight> "

    def __init__(
        self,
        state: WorldState,
        controller: BotController,
        profiles: ProfileSelector,
        log: LogViewer | None = None,
        *,
        color: bool | None = None,
    ) -> None:
        self.state = state
        self.controller = controller
        self.profiles = profiles
        self.log = log if log is not None else LogViewer()
        self.display = StateDisplay(color=color if color is not None else _default_color())
        #: Last successfully loaded profile (None until one is selected).
        self.selected_profile: CalibrationProfile | None = None

    # -- main loop ----------------------------------------------------------

    def run(
        self,
        *,
        input_fn: Callable[[], str | None] | None = None,
        output: Callable[[str], None] | None = None,
        max_iterations: int | None = None,
    ) -> int:
        """Run until the user quits, stdin hits EOF, or *max_iterations*.

        Args:
            input_fn: Returns the next input line, or None for EOF.
                Defaults to the blocking built-in ``input()``.
            output: Where UI text is written. Defaults to ``print``.
            max_iterations: Safety limit on prompt rounds (tests).

        Returns:
            0 on a clean exit.
        """
        read_line = input_fn if input_fn is not None else _read_line_default
        out = output if output is not None else _print_default
        handler = LogViewerHandler(self.log)
        moonlight_logger = logging.getLogger("moonlight")
        moonlight_logger.addHandler(handler)
        try:
            out("MOONLIGHT terminal UI - type 'help' for commands, 'quit' to exit.")
            iterations = 0
            while max_iterations is None or iterations < max_iterations:
                iterations += 1
                out(self._frame())
                line = read_line()
                if line is None:
                    break
                if not self._handle_command(line.strip(), out):
                    break
        finally:
            moonlight_logger.removeHandler(handler)
        out("bye.")
        return 0

    # -- rendering ----------------------------------------------------------

    def _frame(self) -> str:
        """One screen refresh: state block, recent logs, prompt."""
        if self.controller.is_running():
            bot = self.controller.loop.current_state.value
        else:
            bot = "stopped"
        parts = [self.display.render(self.state, bot_state=bot), ""]
        for msg in self.log.get_recent(5):
            parts.append(self.display._paint(f"  {msg}", DIM))
        parts.append(self.PROMPT)
        return "\n".join(parts)

    # -- command handling ----------------------------------------------------

    def _handle_command(self, line: str, out: Callable[[str], None]) -> bool:
        """Handle one command line; return False when the UI should exit."""
        parts = line.split()
        if not parts:
            return True
        cmd, args = parts[0].lower(), parts[1:]
        if cmd in ("quit", "exit", "q"):
            return False
        if cmd == "help":
            out(_HELP_TEXT)
        elif cmd == "start":
            try:
                self.controller.start()
            except RuntimeError as exc:
                self._log(f"start failed: {exc}")
            else:
                self._log("bot started")
        elif cmd == "stop":
            self.controller.stop("ui: stop")
            self._log("stop requested")
        elif cmd == "kill":
            self.controller.kill()
            self._log("KILL SWITCH ACTIVATED")
        elif cmd == "profiles":
            names = self.profiles.list_profiles()
            out("profiles: " + (", ".join(names) if names else "(none found)"))
        elif cmd == "profile":
            self._select_profile(args, out)
        elif cmd == "calibrate":
            self._calibrate(args, out)
        elif cmd == "state":
            out(self.display.render(self.state))
        elif cmd == "log":
            n = 10
            if args:
                try:
                    n = max(0, int(args[0]))
                except ValueError:
                    out(f"invalid count: {args[0]!r}")
                    return True
            recent = self.log.get_recent(n)
            out("\n".join(recent) if recent else "(log empty)")
        elif cmd == "stats":
            out(self._format_stats())
        else:
            out(f"unknown command: {cmd!r} (try 'help')")
        return True

    def _select_profile(self, args: list[str], out: Callable[[str], None]) -> None:
        if not args:
            out("usage: profile <name>")
            return
        name = args[0]
        try:
            self.selected_profile = self.profiles.select_profile(name)
        except (ValueError, CalibrationError) as exc:
            self._log(f"profile error: {exc}")
            out(f"profile error: {exc}")
            return
        self._log(f"profile loaded: {name}")
        out(f"profile loaded: {name}")

    def _calibrate(self, args: list[str], out: Callable[[str], None]) -> None:
        """Launch the interactive calibration window from inside the UI.

        Usage:
            calibrate [name]
            calibrate <name> --screenshot <path>

        Opens an OpenCV window where the user clicks a landmark and drags
        rectangles over each region. On completion the profile is saved to
        ``profiles/<name>.json`` and auto-loaded.
        """
        # Parse args: optional name, optional --screenshot <path>.
        name: str | None = None
        screenshot: str | None = None
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--screenshot":
                if i + 1 < len(args):
                    screenshot = args[i + 1]
                    i += 2
                    continue
                out("usage: --screenshot <path>")
                return
            elif a.startswith("--"):
                out(f"unknown option: {a}")
                return
            elif name is None:
                name = a
            i += 1

        # Prompt for a name if not provided.
        if name is None:
            try:
                name = input("  profile name [default]: ").strip() or "default"
            except EOFError:
                name = "default"

        out(f"  opening calibration window for profile {name!r}...")
        out("  (click the landmark, then drag each region; Esc to cancel)")

        # Load a still image if provided, else capture the live screen.
        try:
            image = load_image_bgr(screenshot) if screenshot else None
        except (CalibrationError, Exception) as exc:  # noqa: BLE001
            out(f"  failed to load screenshot: {exc}")
            return

        try:
            app = CalibrationApp(image_bgr=image)
        except Exception as exc:  # noqa: BLE001 - GUI deps may be missing
            out(f"  calibration unavailable: {exc}")
            return

        profile = app.run()
        if profile is None:
            out("  calibration cancelled (no profile saved).")
            self._log("calibration cancelled")
            return

        try:
            profile.validate()
        except CalibrationError as exc:
            out(f"  calibration incomplete: {exc}")
            self._log("calibration incomplete")
            return

        # Save the profile and auto-load it.
        profiles_dir = self.profiles.directory
        profiles_dir.mkdir(parents=True, exist_ok=True)
        out_path = profiles_dir / f"{name}.json"
        profile.save(out_path)
        out(f"  saved profile to {out_path}")
        self._log(f"profile saved: {name}")

        try:
            self.selected_profile = self.profiles.select_profile(name)
            out(f"  profile loaded: {name} (restart the bot to use it)")
        except (ValueError, CalibrationError) as exc:
            out(f"  saved, but could not auto-load: {exc}")

    def _format_stats(self) -> str:
        s = self.controller.loop.get_stats()
        return "\n".join(
            [
                f"  state: {s['state']}",
                f"  ticks: {s['tick_count']}",
                f"  uptime: {s['uptime_seconds']}s",
                f"  avg tick: {s['avg_tick_time_ms']} ms (max {s['max_tick_time_ms']} ms)",
                f"  actions sent: {s['actions_sent']}",
                f"  dropped (rate limit): {s['actions_dropped_rate_limit']}",
                f"  dropped (unfocused): {s['actions_dropped_unfocused']}",
                f"  kill switch: {s['kill_switch_active']}   stop requested: {s['stop_requested']}",
            ]
        )

    def _log(self, msg: str) -> None:
        self.log.add_log(f"[{time.strftime('%H:%M:%S')}] {msg}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — run as ``python -m moonlight.ui`` (see module notes).

    Until T16 provides ``ArduinoInput``, the loop is wired with
    :class:`moonlight.input.FakeInput` as a placeholder backend, so the UI
    can be developed and tested offline.
    """
    parser = argparse.ArgumentParser(
        prog="moonlight-ui",
        description=(
            "MOONLIGHT terminal UI: start/stop the bot, pick a profile, "
            "watch world state and logs."
        ),
    )
    parser.add_argument("--profile", default=None, help="calibration profile to load at startup")
    parser.add_argument(
        "--profiles-dir",
        type=Path,
        default=None,
        help="profile directory (default: profiles/ next to the package)",
    )
    args = parser.parse_args(argv)

    state = WorldState.default()
    log = LogViewer()
    backend = FakeInput()
    controller = BotController(GameLoop(state, backend))
    selector = ProfileSelector(args.profiles_dir)
    if args.profile is not None:
        try:
            selector.select_profile(args.profile)
            log.add_log(f"[{time.strftime('%H:%M:%S')}] profile loaded: {args.profile}")
        except (ValueError, CalibrationError) as exc:
            log.add_log(f"[{time.strftime('%H:%M:%S')}] profile error: {exc}")
    ui = MoonlightUI(state, controller, selector, log)
    return ui.run()


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    sys.exit(main())
