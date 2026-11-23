"""Property-based exploration of the real GUI window (the Bombadil idea, for Tk).

Hypothesis drives the real :class:`~moonlight.gui.app.GuiApp` with random
sequences of what an operator and the game can do: switch tabs, press
Start / Pause / Stop, press F12, trip the kill switch, lose focus, change
HP and MP, resize the window and wait. After every step it checks properties
that must always hold. A failure is shrunk to the shortest sequence of steps
that still breaks, and Hypothesis prints it as a script to paste into a
regression test.

Only the edges are fake: the bot loop is a real :class:`GameLoop` whose
perceive step writes a scripted screen state and whose only action is
pressing ``w`` (sent through the loop's gated input), wired to a
:class:`FakeInput` that records every wire line. There is no Arduino,
client window, network or screen capture.

Opens a Tk window, so it runs only when ``MOONLIGHT_GUI_EXPLORE=1`` is set
(CI runs it under ``xvfb-run``). Locally on Linux::

    MOONLIGHT_GUI_EXPLORE=1 xvfb-run -a python -m pytest tests/test_gui_explore.py

``MOONLIGHT_GUI_EXPLORE_EXAMPLES`` (default 6) sets how many random runs to
try; raise it (e.g. 200) for a long bug hunt.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

if os.environ.get("MOONLIGHT_GUI_EXPLORE") != "1":
    pytest.skip("opens a Tk window: set MOONLIGHT_GUI_EXPLORE=1", allow_module_level=True)
if sys.platform == "linux" and not os.environ.get("DISPLAY"):
    pytest.skip("no X display (run under xvfb-run)", allow_module_level=True)

from hypothesis import HealthCheck, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402
from hypothesis.stateful import (  # noqa: E402
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
)

import moonlight.gui.app as app_mod  # noqa: E402
import moonlight.gui.dashboard_tab as dashboard_mod  # noqa: E402
from moonlight.core.loop import GameLoop, LoopConfig  # noqa: E402
from moonlight.core.state import WorldState  # noqa: E402
from moonlight.gui.app import STATE_COLORS, GuiApp, nav_colors  # noqa: E402
from moonlight.gui.state_bridge import HardwareController  # noqa: E402
from moonlight.hardware.input import FakeInput  # noqa: E402

EXAMPLES = int(os.environ.get("MOONLIGHT_GUI_EXPLORE_EXAMPLES", "6"))
#: Wire lines that press something; ``X`` (release all) and ``P`` (ping) do not.
ACTION_COMMANDS = frozenset("KDUMACHR")
#: Long enough for the 100 ms poller and a 50 ms bot tick to see a change.
SETTLE_S = 0.25
#: One poller pass may not take longer than this (it runs on the GUI thread).
POLL_BUDGET_S = 0.5
#: The button states that go with each top-bar state: (Start, Pause, Stop).
BUTTONS_FOR_STATE = {
    "Stopped": (True, False, False),
    "Running": (False, True, True),
    "Waiting for game": (False, True, True),
    "Paused": (True, False, True),
}


def is_action(line: str) -> bool:
    return line.split(" ", 1)[0] in ACTION_COMMANDS


class FakeGame:
    """The scripted screen the fake loop perceives, and the wire log."""

    def __init__(self) -> None:
        self.wire = FakeInput()
        self.focused = True
        self.hp = 100.0
        self.mp = 100.0
        self.f12_presses = 0
        self.loops: list[GameLoop] = []

    def lines(self) -> list[str]:
        return list(self.wire.sent_lines)  # the bot thread appends; read a copy

    def build_loop(
        self, hardware: HardwareController, state: WorldState, raise_client: bool = False
    ) -> GameLoop:
        def perceive() -> None:
            with state.lock():
                state.screen_ok = self.focused
                state.client_focused = self.focused
                state.hp_percent = self.hp
                state.mp_percent = self.mp

        loop = GameLoop(
            state,
            self.wire,
            LoopConfig(enable_f12_monitor=False),
            perceive=perceive,
            move_provider=lambda _snap: True,
            move_action=lambda: loop.input.press_combo("w"),
        )
        self.loops.append(loop)
        return loop

    def f12_pressed(self) -> bool:
        if self.f12_presses:
            self.f12_presses -= 1
            return True
        return False


def no_position_reader(_state: WorldState) -> Callable[[], None]:
    raise RuntimeError("no game window in this test")


class GuiExplorer(RuleBasedStateMachine):
    """One random operator session against a fresh window."""

    def __init__(self) -> None:
        super().__init__()
        self.game = FakeGame()
        self.callback_errors: list[str] = []
        self.poll_times: list[float] = []
        #: While Stop holds the bot: wire lines before this index may press keys.
        self.quiet_from: int | None = None
        self._patches = [
            (app_mod, "build_gui_loop", self.game.build_loop),
            (app_mod, "build_position_reader", no_position_reader),
            (app_mod, "f12_pressed", self.game.f12_pressed),
            (app_mod, "RETRY_S", 0.3),
            (dashboard_mod, "public_ip", lambda: "203.0.113.1"),
        ]
        self._saved = [(mod, name, getattr(mod, name)) for mod, name, _ in self._patches]
        for mod, name, value in self._patches:
            setattr(mod, name, value)
        self.app = GuiApp()
        self.app.root.report_callback_exception = self._callback_error
        poll: Callable[[], None] = self.app._poll

        def timed_poll() -> None:
            t0 = time.perf_counter()
            poll()
            self.poll_times.append(time.perf_counter() - t0)

        self.app._poll = timed_poll  # type: ignore[method-assign]
        self.pump(SETTLE_S)

    def _callback_error(self, kind: Any, error: Any, _trace: Any) -> None:
        self.callback_errors.append(f"{kind.__name__}: {error}")

    def pump(self, seconds: float) -> None:
        """Run the Tk event loop (and with it the poller) for *seconds*."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.root.update()
            time.sleep(0.01)

    def teardown(self) -> None:
        try:
            self.app.on_close()
            for loop in self.game.loops:  # a loop closed with the window joins in 5 s
                if loop.kill_switch_active or loop.stop_requested:
                    continue
                pytest.fail("a bot loop was left running after the window closed")
            alive = [t.name for t in threading.enumerate() if t.name == "moonlight-gui-bot"]
            assert not alive, f"bot thread outlived the window: {alive}"
        finally:
            for mod, name, value in self._saved:
                setattr(mod, name, value)

    # -- what the operator and the game do ---------------------------------

    @rule(data=st.data())
    def switch_tab(self, data: st.DataObject) -> None:
        title = data.draw(st.sampled_from(sorted(self.app._nav)), label="tab")
        self.app._nav[title].invoke()
        self.pump(SETTLE_S)
        assert self.app._current == title

    @rule()
    def press_start(self) -> None:
        self.app.btn_start.invoke()
        self.pump(SETTLE_S)
        self.quiet_from = None if not self.app._held else self.quiet_from

    @rule()
    def press_pause(self) -> None:
        self.app.btn_pause.invoke()
        self.pump(SETTLE_S)

    @rule()
    def press_stop(self) -> None:
        enabled = self.app.btn_stop.cget("state") == "normal"
        self.app.btn_stop.invoke()
        self.pump(SETTLE_S)
        if enabled:
            self.expect_released("Stop")

    @rule()
    def press_f12(self) -> None:
        was_held = self.app._held
        self.game.f12_presses += 1
        self.pump(SETTLE_S)
        if was_held and not self.app._held:
            self.quiet_from = None

    @precondition(lambda self: self.app.ctx.controller is not None)
    @rule()
    def kill_switch(self) -> None:
        """The Arduino kill pin (or the loop's own F12 monitor) fires."""
        ctrl = self.app.ctx.controller
        assert ctrl is not None
        ctrl.loop.kill()
        self.pump(SETTLE_S)
        self.expect_released("kill switch")

    @rule(focused=st.booleans())
    def game_focus(self, focused: bool) -> None:
        self.game.focused = focused
        self.pump(SETTLE_S)

    @rule(hp=st.floats(0, 100), mp=st.floats(0, 100))
    def vitals(self, hp: float, mp: float) -> None:
        self.game.hp, self.game.mp = hp, mp
        self.pump(SETTLE_S)

    @rule(width=st.integers(880, 1600), height=st.integers(580, 1000))
    def resize(self, width: int, height: int) -> None:
        self.app.root.geometry(f"{width}x{height}")
        self.pump(SETTLE_S)

    @rule(seconds=st.sampled_from([0.1, 0.5, 1.0]))
    def wait(self, seconds: float) -> None:
        self.pump(seconds)

    def expect_released(self, why: str) -> None:
        """After Stop or the kill switch: an ``X`` follows the last key press."""
        lines = self.game.lines()
        pressed = [i for i, line in enumerate(lines) if is_action(line)]
        if pressed:
            assert "X" in lines[pressed[-1] + 1 :], f"{why} did not release all inputs"
        self.quiet_from = len(lines)

    # -- what must always hold ----------------------------------------------

    @invariant()
    def no_callback_errors(self) -> None:
        assert not self.callback_errors, self.callback_errors

    @invariant()
    def no_poll_errors(self) -> None:
        bad = [line for line in self.app.log.text(500).splitlines() if "poll error" in line]
        assert not bad, bad

    @invariant()
    def poller_stays_fast(self) -> None:
        slow = [t for t in self.poll_times if t > POLL_BUDGET_S]
        assert not slow, f"poller pass took {max(slow):.2f} s (budget {POLL_BUDGET_S} s)"

    @invariant()
    def stop_means_nothing_is_pressed(self) -> None:
        if not self.app._held:
            self.quiet_from = None
            return
        if self.quiet_from is None:
            return
        late = [line for line in self.game.lines()[self.quiet_from :] if is_action(line)]
        assert not late, f"pressed while stopped: {late}"

    @invariant()
    def top_bar_matches_buttons(self) -> None:
        state = self.app.lbl_state.cget("text")
        assert state in STATE_COLORS, state
        expected = BUTTONS_FOR_STATE[state]
        actual = tuple(
            btn.cget("state") == "normal"
            for btn in (self.app.btn_start, self.app.btn_pause, self.app.btn_stop)
        )
        assert actual == expected, f"{state}: Start/Pause/Stop enabled = {actual}"

    @invariant()
    def stopped_shows_stopped(self) -> None:
        if self.app._held:
            assert self.app.lbl_state.cget("text") == "Stopped"

    @invariant()
    def one_tab_highlighted(self) -> None:
        active = nav_colors(active=True)["fg_color"]
        lit = [t for t, btn in self.app._nav.items() if btn.cget("fg_color") == active]
        assert lit == [self.app._current], lit


GuiExplorer.TestCase.settings = settings(
    max_examples=EXAMPLES,
    stateful_step_count=20,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
TestGuiExplorer = GuiExplorer.TestCase
