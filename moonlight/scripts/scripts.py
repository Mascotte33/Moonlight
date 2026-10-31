"""User scripts: one ``.py`` file each in ``scripts/``, run at walk priority.

A script file defines ``run(bot)``, a generator. Each ``yield`` hands the
tick back to the loop::

    def run(bot):
        if bot.hp < 50:
            yield bot.press("f3")
        yield 2                     # wait 2 seconds
        spot = bot.find("rope.png") # a picture in scripts/, None when not seen
        if spot:
            yield bot.click(*spot)
        yield bot.say("hi")
        yield bot.goto("town")      # jump the route to label town

``bot.press`` / ``say`` / ``click`` / ``goto`` return the wire commands to
send; the runner sends one per tick, so the rate limit never drops them. A
yielded number waits that many seconds. ``bot.hp``, ``bot.mana`` and
``bot.cap`` (used capacity %, 0 when unknown) read the current tick;
``bot.count("mana")`` the action-bar potion total.

The loop runs a script only in MOVE and IDLE, after healing: it pauses
walking (the route holds) and pauses itself during a fight or looting.
Scripts start from a ``script`` waypoint or ticked on the Scripts tab
(``ScriptsConfig``): a ticked one runs again its pause after it ended.
Each start reads the file again, so edits apply without a restart. A
script that does not start or fails stops, is unticked (``failed``) and
the bot goes on.

Scripts are trusted local code (no sandbox).
"""

# doc: docs/prs/scripts.md#script-api

from __future__ import annotations

import ast
import functools
import importlib.util
import logging
import re
import time
import traceback
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from moonlight.core.state import WorldState
from moonlight.hardware.input import ClickSide, InputBackend

if TYPE_CHECKING:
    from moonlight.cavebot.cavebot import Cavebot
    from moonlight.hardware.clicker import ClickController
    from moonlight.scripts.rotation import Rotation

__all__ = [
    "DEFAULT_PAUSE_MS",
    "DEFAULT_SCRIPTS_DIR",
    "MIN_PAUSE_MS",
    "Bot",
    "ScriptRunner",
    "ScriptsConfig",
    "parse_timers",
    "script_error",
]

log = logging.getLogger(__name__)

DEFAULT_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"

#: How close a picture must match (cv2 normalized correlation).
_FIND_THRESHOLD = 0.9

#: A ticked script's pause after it ended, in ms, when none is set.
DEFAULT_PAUSE_MS = 1000
#: The bot reads the screen every 50 ms: a shorter pause does nothing more.
MIN_PAUSE_MS = 50
#: Seconds a Test now waits for the client to be in front and read.
TEST_WAIT = 5.0

Command = Callable[[], object]


# doc: docs/prs/scripts-page.md#ticked-scripts
@dataclass(frozen=True)
class ScriptsConfig:
    #: Scripts that run on their own, top of the list first.
    ticked: tuple[str, ...] = ()
    #: Script name -> its pause in ms after it ended (none: ``DEFAULT_PAUSE_MS``).
    pause_ms: dict[str, int] = field(default_factory=dict)

    def validate(self) -> None:
        for name in (*self.ticked, *self.pause_ms):
            if not re.fullmatch(r"\w+", name):
                raise ValueError(f"script name {name!r} is letters, digits and _")
        for name, ms in self.pause_ms.items():
            if ms < MIN_PAUSE_MS:
                raise ValueError(f"script {name}: pause {ms} ms is under {MIN_PAUSE_MS} ms")

    def timers(self) -> list[tuple[str, float]]:
        """The ticked scripts with their pause in seconds, in order."""
        return [(n, self.pause_ms.get(n, DEFAULT_PAUSE_MS) / 1000) for n in self.ticked]


def script_error(source: str) -> str | None:
    """Why *source* cannot run as a script (``line 3: invalid syntax``), or None."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return f"line {exc.lineno}: {exc.msg}"
    if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
        return "no def run(bot)"
    return None


def parse_timers(text: str) -> list[tuple[str, float]]:
    """``"eat 60, rope 5"`` -> ``[("eat", 60.0), ("rope", 5.0)]`` (the old timer box).

    Raises:
        ValueError: For an entry that is not a name and a number of seconds > 0.
    """
    timers = []
    for item in filter(None, (part.strip() for part in text.split(","))):
        m = re.fullmatch(r"(\w+)\s+(\d+(?:\.\d+)?)", item)
        if m is None or float(m[2]) <= 0:
            raise ValueError(f"timed script {item!r} is not 'name seconds'")
        timers.append((m[1], float(m[2])))
    return timers


class Bot:
    """What a script sees: readings of this tick and commands to yield."""

    def __init__(self, runner: ScriptRunner) -> None:
        self._runner = runner

    @property
    def _snap(self) -> WorldState:
        return self._runner.snap

    @property
    def hp(self) -> float:
        return self._snap.hp_percent

    @property
    def mana(self) -> float:
        return self._snap.mp_percent

    @property
    def cap(self) -> float:
        cap = self._snap.capacity
        return cap.percent if cap.max_weight > 0 else 0.0

    @property
    def in_game(self) -> bool:
        """The game screen reads (False on the login screen)."""
        return self._snap.screen_ok and self._snap.hp_percent > 0

    def type(self, text: str, *, secret: bool = False) -> list[Command]:
        """Type *text* (letters and digits, no Enter); *secret* keeps it out of the command log."""
        if not re.fullmatch(r"[A-Za-z0-9]*", text):
            raise ValueError("type text is letters and digits")
        keys = [f"shift+{ch.lower()}" if ch.isupper() else ch for ch in text]
        press = _secret_press if secret else _press
        return [functools.partial(press, self._runner, k) for k in keys]

    def press(self, key: str) -> list[Command]:
        return [functools.partial(self._runner.input.press_combo, key)]

    def say(self, text: str) -> list[Command]:
        from moonlight.cavebot.cavebot import label_error, say_keys

        if bad := label_error("say", text):
            raise ValueError(bad)
        return [functools.partial(self._runner.input.press_combo, k) for k in say_keys(text)]

    def click(self, x: int, y: int, right: bool = False) -> list[Command]:
        """Click client-window pixel *x*, *y* (what the Edit route cursor readout shows)."""
        clicker = self._runner.clicker
        if clicker is None or not clicker.absolute:
            raise RuntimeError("bot.click needs the absolute clicker")
        point = clicker.to_wire((x, y))
        side = ClickSide.RIGHT if right else ClickSide.LEFT
        send = self._runner.input
        return [functools.partial(send.mouse_to, *point), functools.partial(send.click, side)]

    def goto(self, label: str) -> list[Command]:
        return [functools.partial(self._runner.goto, label)]

    def count(self, potion: str) -> int | None:
        """The action-bar total of ``"health"`` or ``"mana"`` potions; None when unknown."""
        if potion not in ("health", "mana"):
            raise ValueError("count reads 'health' or 'mana'")
        counts = self._runner.potion_counts
        if counts is None:
            raise RuntimeError("bot.count needs a screen capture")
        return counts()[potion]

    # doc: docs/prs/scripts.md#find-picture
    def find(self, image: str) -> tuple[int, int] | None:
        """Centre of picture *image* (a file in ``scripts/``) on screen, or None."""
        import cv2

        grab = self._runner.grab
        if grab is None:
            raise RuntimeError("bot.find needs a screen capture")
        needle = cv2.imread(str(self._runner.scripts_dir / image), cv2.IMREAD_COLOR)
        if needle is None:
            raise FileNotFoundError(self._runner.scripts_dir / image)
        frame = cv2.cvtColor(np.ascontiguousarray(grab()), cv2.COLOR_RGB2BGR)
        scores = cv2.matchTemplate(frame, needle, cv2.TM_CCOEFF_NORMED)
        _, best, _, (x, y) = cv2.minMaxLoc(scores)
        if best < _FIND_THRESHOLD:
            return None
        h, w = needle.shape[:2]
        return (x + w // 2, y + h // 2)


def _press(runner: ScriptRunner, key: str) -> None:
    runner.input.press_combo(key)


def _secret_press(runner: ScriptRunner, key: str) -> None:
    """Press *key* with the command log (``LoggedInput.quiet``) muted."""
    logged = []
    backend: Any = runner.input
    while backend is not None:  # the wire wrappers keep the next one in .backend / .inner
        if "quiet" in vars(type(backend)):
            logged.append(backend)
        backend = vars(backend).get("backend") or vars(backend).get("inner")
    for b in logged:
        b.quiet = True
    try:
        runner.input.press_combo(key)
    finally:
        for b in logged:
            b.quiet = False


class ScriptRunner:
    """Runs one user script at a time, one wire command per tick."""

    def __init__(
        self,
        scripts_dir: Path | str | None = None,
        *,
        clicker: ClickController | None = None,
        grab: Callable[[], np.ndarray] | None = None,
        timers: list[tuple[str, float]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.scripts_dir = Path(scripts_dir) if scripts_dir else DEFAULT_SCRIPTS_DIR
        self.clicker = clicker
        self.grab = grab
        #: The action-bar potion totals for ``bot.count``.
        self.potion_counts: Callable[[], dict[str, int | None]] | None = None
        #: Ticked scripts: (name, pause in seconds after it ended), in order.
        self.timers = timers or []
        self._clock = clock
        self._next_run: dict[str, float] = {}
        #: Script name -> why it stopped (``line 3: ZeroDivisionError: ...``);
        #: the Scripts tab unticks it and shows the error.
        self.failed: dict[str, str] = {}
        #: A script the Scripts tab's Test now asked for, and until when it may
        #: wait for the client; it starts first. See :meth:`request_test`.
        self.test_request: str | None = None
        self._test_until = 0.0
        self._testing = False
        #: Why the last Test now did not run (the tab shows and clears it).
        self.test_missed: str | None = None
        #: The client window is in front (the keys go to the window in front).
        self.client_is_foreground: Callable[[], bool] = lambda: True
        self.running: str | None = None
        self._script: Generator[Any, Any, None] | None = None
        self._commands: Iterator[Command] = iter(())
        self._pending: Command | None = None
        self._wait_until = 0.0
        self.snap = WorldState()
        self.input: InputBackend
        self.cavebot: Cavebot | None = None
        #: Account rotation: the ``rotate`` script is its switch (docs/prs/rotation.md).
        self.rotation: Rotation | None = None

    def start(self, name: str) -> bool:
        """Start script *name* (read fresh from disk); False when it cannot run."""
        path = self.scripts_dir / f"{name}.py"
        try:
            if name == "rotate" and self.rotation is not None:
                self._script = self.rotation.run(Bot(self))  # built in, no file
            else:
                spec = importlib.util.spec_from_file_location(
                    f"moonlight_user_script_{name}", path
                )
                if spec is None or spec.loader is None:
                    raise ImportError(f"cannot load {path}")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._script = module.run(Bot(self))
        except (Exception, SystemExit) as exc:  # user code must not crash the bot
            self._fail(name, exc, "is broken, skipped")
            return False
        log.info("script %s started", name)
        self.running = name
        self._commands, self._pending, self._wait_until = iter(()), None, 0.0
        return True

    def stop(self) -> None:
        if self.running is not None:
            log.info("script %s ended", self.running)
            pause = dict(self.timers).get(self.running)
            if pause is not None:
                self._next_run[self.running] = self._clock() + pause
        self.running = self._script = None
        self._pending = None
        self._testing = False

    @property
    def testing(self) -> bool:
        """A Test now script is asked for or runs (it runs while paused too)."""
        return self.test_request is not None or self._testing

    # doc: docs/prs/scripts-page.md#test-now
    def request_test(self, name: str) -> None:
        """Run *name* once as soon as the client is in front and read, within ``TEST_WAIT``."""
        self.test_request, self.test_missed = name, None
        self._test_until = self._clock() + TEST_WAIT

    def drop_late_test(self) -> None:
        """Drop the asked-for test when ``TEST_WAIT`` passed (the loop may be
        paused all that time, so the Scripts tab calls this too)."""
        name = self.test_request
        if name is not None and self._clock() >= self._test_until:
            self.test_request = None
            self.test_missed = f"{name} not tested: Tibia was not in front and readable"
            log.warning("script %s", self.test_missed)

    def _start_test(self) -> None:
        """Start the asked-for test once the client is in front."""
        self.drop_late_test()
        name = self.test_request
        if name is not None and self.client_is_foreground():
            self.test_request = None
            self._testing = self.start(name)

    # doc: docs/prs/scripts-page.md#ticked-scripts
    def poll(self) -> str | None:
        """Start the asked-for test, else a ticked script that is due, when none
        runs; the running script's name. A pending test holds the ticked ones."""
        if self.running is None and self.test_request is not None:
            self._start_test()
        elif self.running is None:
            now = self._clock()
            for name, _pause in self.timers:
                if now >= self._next_run.get(name, 0.0) and self.start(name):
                    break
        return self.running

    def _fail(self, name: str, exc: BaseException, what: str) -> None:
        """Log why script *name* stopped, keep it in ``failed`` and untick it."""
        lines = [
            f.lineno for f in traceback.extract_tb(exc.__traceback__)
            if Path(f.filename).stem == name
        ]  # fmt: skip
        if isinstance(exc, SyntaxError) and exc.lineno is not None:
            lines.append(exc.lineno)
        where = f"line {lines[-1]}: " if lines else ""
        self.failed[name] = f"{where}{type(exc).__name__}: {exc}"
        log.warning("script %s %s: %s", name, what, self.failed[name])
        self.timers = [(n, p) for n, p in self.timers if n != name]

    def goto(self, label: str) -> None:
        if self.cavebot is None:
            log.warning("script %s: goto %r without a cavebot", self.running, label)
        else:
            self.cavebot.goto(label)

    def step(self, snap: WorldState, input: InputBackend) -> bool:  # noqa: A002
        """One tick of the running script: one command, a wait, or the script's next yield.

        True when a command went out (the loop counts it as an action).
        """
        if self._script is None:
            return False
        self.snap, self.input = snap, input
        try:
            if self._pending is None:
                self._pending = next(self._commands, None)
            if self._pending is not None:
                dropped = getattr(input, "dropped", 0)
                self._pending()
                if getattr(input, "dropped", 0) != dropped:
                    return False  # the wire limiter dropped it: send it again
                self._pending = None
                return True
            if self._clock() < self._wait_until:
                return False
            got = next(self._script)
            if isinstance(got, int | float):
                self._wait_until = self._clock() + got
            elif got is not None:
                self._commands = iter(got)
        except StopIteration:
            self.stop()
        except Exception as exc:
            assert self.running is not None
            self._fail(self.running, exc, "failed")
            self.stop()
        return False
