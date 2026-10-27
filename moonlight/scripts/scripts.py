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
``bot.cap`` (used capacity %, 0 when unknown) read the current tick.

The loop runs a script only in MOVE and IDLE, after healing: it pauses
walking (the route holds) and pauses itself during a fight or looting.
Scripts start from a ``script`` waypoint or on a timer (``ScriptsConfig``).
Each start reads the file again, so edits apply without a restart. An
error in a script is logged and stops that script only.

Scripts are trusted local code (no sandbox).
"""

# doc: docs/prs/scripts.md#script-api

from __future__ import annotations

import functools
import importlib.util
import logging
import re
import time
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from moonlight.core.state import WorldState
from moonlight.hardware.input import ClickSide, InputBackend

if TYPE_CHECKING:
    from moonlight.cavebot.cavebot import Cavebot
    from moonlight.hardware.clicker import ClickController

__all__ = ["DEFAULT_SCRIPTS_DIR", "Bot", "ScriptRunner", "ScriptsConfig", "parse_timers"]

log = logging.getLogger(__name__)

DEFAULT_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"

#: How close a picture must match (cv2 normalized correlation).
_FIND_THRESHOLD = 0.9

Command = Callable[[], object]


@dataclass(frozen=True)
class ScriptsConfig:
    #: Timed scripts, ``"name seconds, name seconds"``: each runs every N s.
    timers: str = ""

    def validate(self) -> None:
        parse_timers(self.timers)


def parse_timers(text: str) -> list[tuple[str, float]]:
    """``"eat 60, rope 5"`` -> ``[("eat", 60.0), ("rope", 5.0)]``.

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
        self.timers = timers or []
        self._clock = clock
        self._next_run: dict[str, float] = {}
        self.running: str | None = None
        self._script: Generator[Any, Any, None] | None = None
        self._commands: Iterator[Command] = iter(())
        self._pending: Command | None = None
        self._wait_until = 0.0
        self.snap = WorldState()
        self.input: InputBackend
        self.cavebot: Cavebot | None = None

    def names(self) -> list[str]:
        """The script names in the scripts dir."""
        return sorted(p.stem for p in self.scripts_dir.glob("*.py"))

    def start(self, name: str) -> bool:
        """Start script *name* (read fresh from disk); False when it cannot run."""
        path = self.scripts_dir / f"{name}.py"
        try:
            spec = importlib.util.spec_from_file_location(f"moonlight_user_script_{name}", path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._script = module.run(Bot(self))
        except (Exception, SystemExit):  # user code must not crash the bot
            log.exception("script %s did not start", name)
            return False
        log.info("script %s started", name)
        self.running = name
        self._commands, self._pending, self._wait_until = iter(()), None, 0.0
        return True

    def stop(self) -> None:
        if self.running is not None:
            log.info("script %s ended", self.running)
        self.running = self._script = None
        self._pending = None

    def poll(self) -> str | None:
        """Start a timed script that is due when none runs; the running script's name."""
        if self.running is None:
            now = self._clock()
            for name, every in self.timers:
                if now >= self._next_run.get(name, 0.0):
                    self._next_run[name] = now + every
                    if self.start(name):
                        break
        return self.running

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
        except Exception:
            log.exception("script %s failed", self.running)
            self.stop()
        return False
