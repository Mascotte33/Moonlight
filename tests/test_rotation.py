"""Offline tests for account rotation: the timer, the switch, failed logins."""

# doc: docs/prs/rotation.md#rotation

from __future__ import annotations

from pathlib import Path

import pytest

from moonlight.core.state import WorldState
from moonlight.gui.loop_factory import LoggedInput
from moonlight.hardware.input import FakeInput
from moonlight.scripts.rotation import LIST_TOP, Account, Rotation, RotationConfig
from moonlight.scripts.scripts import ScriptRunner

A = Account("Abc1", "Pw9", character=2, minutes=1.0)
B = Account("def", "XY", character=1, minutes=2.0)
LOGIN_B = ["K ctrl+a", "K d", "K e", "K f", "K tab"]
LOGIN_B += ["K ctrl+a", "K shift+x", "K shift+y", "K enter"]  # the password XY
PICK_1 = ["K up"] * LIST_TOP + ["K enter"]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def setup(tmp_path: Path) -> tuple[ScriptRunner, Rotation, Clock]:
    clock = Clock()
    r = ScriptRunner(tmp_path, clock=clock)
    r.rotation = Rotation(RotationConfig(accounts=(A, B)), clock=clock)
    return r, r.rotation, clock


def play(r: ScriptRunner, inp: object, clock: Clock, in_game: bool, seconds: float) -> None:
    """Step the runner for *seconds* at 10 ticks/s, the game shown or not."""
    snap = WorldState(screen_ok=in_game, hp_percent=100.0 if in_game else 0.0)
    for _ in range(int(seconds * 10)):
        r.step(snap, inp)  # type: ignore[arg-type]
        clock.now += 0.1


def test_validate() -> None:
    RotationConfig(accounts=(A, B)).validate()
    for bad in (
        RotationConfig(accounts=(A,)),
        RotationConfig(accounts=(A, Account("a.b", "x"))),
        RotationConfig(accounts=(A, Account("ab", ""))),
        RotationConfig(accounts=(A, Account("ab", "x", character=0))),
        RotationConfig(accounts=(A, Account("ab", "x", minutes=0))),
        RotationConfig(accounts=(A, B), logout_hotkey=""),
    ):
        with pytest.raises(ValueError):
            bad.validate()


def test_rotate_waits_for_the_time_then_switches(tmp_path: Path) -> None:
    r, rot, clock = setup(tmp_path)
    inp = FakeInput()
    assert not rot.refill_due()
    r.start("rotate")
    play(r, inp, clock, True, 1)
    assert inp.sent_lines == [] and r.running is None  # not due: the route walks on
    clock.now = 60.0
    assert rot.refill_due() and not rot.refill_due()  # asked once
    r.start("rotate")
    play(r, inp, clock, True, 0.5)
    assert inp.sent_lines == ["K ctrl+l"]
    play(r, inp, clock, False, 10)  # logged out; the character list opens
    assert inp.sent_lines == ["K ctrl+l", *LOGIN_B, *PICK_1]
    play(r, inp, clock, True, 1)
    assert r.running is None and rot.index == 1 and not rot.due()


def test_a_failed_login_is_skipped_and_all_failing_stops(tmp_path: Path) -> None:
    r, rot, clock = setup(tmp_path)
    inp = FakeInput()
    clock.now = 60.0
    r.start("rotate")
    play(r, inp, clock, True, 0.2)
    play(r, inp, clock, False, 90)  # B, then A, never log in
    assert inp.sent_lines.count("K esc") == 2 and inp.sent_lines.count("K ctrl+l") == 1
    assert rot.failed and r.running is None and rot.index == 0


def test_still_in_game_after_logout_keeps_the_account(tmp_path: Path) -> None:
    r, rot, clock = setup(tmp_path)
    clock.now = 60.0
    r.start("rotate")
    play(r, FakeInput(), clock, True, 12)
    assert r.running is None and rot.index == 0 and not rot.due()


def test_the_password_never_reaches_the_command_log(tmp_path: Path) -> None:
    r, _, clock = setup(tmp_path)
    lines: list[str] = []
    clock.now = 60.0
    r.start("rotate")
    play(r, LoggedInput(FakeInput(), lines.append), clock, True, 0.2)
    play(r, LoggedInput(FakeInput(), lines.append), clock, False, 10)
    assert "input: press d" in lines  # the login is logged
    assert "input: press shift+x" not in lines and lines.count("input: press (hidden)") == 2
