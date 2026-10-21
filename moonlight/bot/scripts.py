"""User script loader (T27.D).

Loads user ``.py`` files from a ``scripts/`` directory, each defining one
:class:`BotScript` subclass. Scripts run at WALK level (lowest priority):
:meth:`ScriptRunner.tick` does nothing while a higher-priority behaviour
(HEAL/ALARM/ATTACK/LOOT) is active, and the script's input is wrapped so it
is a no-op unless the client is focused and the screen is ok (fail-closed).

Scripts start either manually (:meth:`ScriptRunner.start`, e.g. from a
cavebot waypoint) or via declarative :class:`Trigger` rules such as
"when ``hp_percent < 30`` run ``heal_more`` for 5 ticks". Any exception in
a user file or its ``tick`` is logged and swallowed; the game loop never
sees it. Not wired into ``loop.py``; call ``runner.tick(state, input)``.

Scripts are trusted local code (no sandbox): the controlled namespace only
prevents accidental pollution of ``sys.modules``.
"""

from __future__ import annotations

import importlib.util
import logging
import operator
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moonlight.bot.state import WorldState
from moonlight.hardware.input import ClickSide, InputBackend

__all__ = [
    "BotScript",
    "Condition",
    "ScriptRunner",
    "Trigger",
]

log = logging.getLogger(__name__)

DEFAULT_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"

_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}


class BotScript:
    """Base class for user scripts; override :meth:`tick`."""

    def tick(self, state: WorldState, input: InputBackend) -> None:  # noqa: A002
        """Called once per loop tick while the script is running."""


@dataclass(frozen=True)
class Condition:
    """``getattr(state, field) <op> value``; false if the field is missing."""

    field: str
    op: str
    value: float

    def __post_init__(self) -> None:
        if self.op not in _OPS:
            raise ValueError(f"unsupported operator {self.op!r}")
        if self.field.startswith("_"):
            raise ValueError(f"private field {self.field!r}")

    def evaluate(self, state: WorldState) -> bool:
        try:
            return bool(_OPS[self.op](getattr(state, self.field), self.value))
        except (AttributeError, TypeError):
            return False


@dataclass(frozen=True)
class Trigger:
    """When ``condition`` holds, run script ``script`` for ``ticks`` ticks."""

    condition: Condition
    script: str
    ticks: int = 1


class _GuardedInput(InputBackend):
    """Delegates to ``inner`` only while the state is focused and screen ok."""

    def __init__(self, inner: InputBackend, state: WorldState) -> None:
        self._inner = inner
        self._state = state

    @property
    def _open(self) -> bool:
        return self._state.client_focused and self._state.screen_ok

    def press_combo(self, combo: str) -> None:
        if self._open:
            self._inner.press_combo(combo)

    def hold_combo(self, combo: str) -> None:
        if self._open:
            self._inner.hold_combo(combo)

    def release_combo(self, combo: str) -> None:
        # Releasing is always safe, never blocked.
        self._inner.release_combo(combo)

    def mouse_move(self, dx: int, dy: int) -> None:
        if self._open:
            self._inner.mouse_move(dx, dy)

    def click(self, side: ClickSide) -> None:
        if self._open:
            self._inner.click(side)

    def ping(self) -> bool:
        return self._inner.ping()

    def release_all(self) -> None:
        self._inner.release_all()


class ScriptRunner:
    """Loads user scripts and runs them at WALK priority."""

    def __init__(
        self,
        scripts_dir: Path | str | None = None,
        triggers: list[Trigger] | None = None,
    ) -> None:
        self.scripts_dir = Path(scripts_dir) if scripts_dir else DEFAULT_SCRIPTS_DIR
        self.triggers: list[Trigger] = list(triggers or [])
        self.scripts: dict[str, BotScript] = {}
        self.active: str | None = None
        self.remaining = 0

    def load_all(self) -> list[str]:
        """Load every ``*.py`` in the scripts dir; return loaded names."""
        if not self.scripts_dir.is_dir():
            return []
        for path in sorted(self.scripts_dir.glob("*.py")):
            self.load(path)
        return sorted(self.scripts)

    def load(self, path: Path | str) -> bool:
        """Load one file; True on success. Never raises."""
        path = Path(path)
        name = path.stem
        try:
            spec = importlib.util.spec_from_file_location(
                f"moonlight_user_script_{name}", path
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for obj in vars(module).values():
                if (
                    isinstance(obj, type)
                    and issubclass(obj, BotScript)
                    and obj is not BotScript
                    and obj.__module__ == module.__name__
                ):
                    self.scripts[name] = obj()
                    return True
            log.warning("script %s defines no BotScript subclass", path)
        except (Exception, SystemExit) as exc:  # user code must not crash us
            log.exception("failed to load script %s: %s", path, exc)
        return False

    def start(self, name: str, ticks: int = 1) -> bool:
        """Start a loaded script for ``ticks`` ticks (e.g. from a waypoint)."""
        if name not in self.scripts or ticks < 1:
            return False
        self.active, self.remaining = name, ticks
        return True

    def stop(self) -> None:
        self.active, self.remaining = None, 0

    def tick(
        self,
        state: WorldState,
        input: InputBackend,  # noqa: A002
        higher_priority_active: bool = False,
    ) -> bool:
        """Run one tick; True if a user script ran.

        While ``higher_priority_active`` the script yields: nothing runs and
        no ticks are consumed. Input is blocked unless focused and screen ok;
        triggers are only evaluated in that safe state.
        """
        if higher_priority_active:
            return False
        safe = state.client_focused and state.screen_ok
        if self.active is None and safe:
            for trig in self.triggers:
                if trig.condition.evaluate(state) and self.start(
                    trig.script, trig.ticks
                ):
                    break
        if self.active is None:
            return False
        script = self.scripts[self.active]
        self.remaining -= 1
        if self.remaining <= 0:
            self.active = None
        try:
            script.tick(state, _GuardedInput(input, state))
        except Exception:
            log.exception("user script raised in tick")
        return True
