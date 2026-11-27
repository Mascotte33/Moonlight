"""Offline tests for the stamina switch: the accounts file, the trigger, the restart."""

# doc: docs/prs/stamina-switch.md#stamina-switch

from __future__ import annotations

from pathlib import Path

import pytest

from moonlight.core.state import WorldState
from moonlight.gui.loop_factory import LoggedInput
from moonlight.hardware.input import FakeInput
from moonlight.perception.stamina import LOW_STAMINA_MINUTES
from moonlight.scripts.rotation import (
    LIST_TOP,
    Account,
    Rotation,
    RotationConfig,
    load_accounts,
    stamina_is_low,
)
from moonlight.scripts.scripts import ScriptRunner

EXE = Path("tibia/client_launcher.exe")
LOGIN_B = ["K ctrl+a", "K d", "K shift+minus", "K e", "K minus", "K f", "K shift+2"]
LOGIN_B += ["K x", "K period", "K i", "K o", "K tab"]  # the email login d_e-f@x.io
LOGIN_B += ["K ctrl+a", "K shift+x", "K shift+y", "K enter"]  # the password XY
PICK_1 = ["K up"] * LIST_TOP + ["K enter"]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Client:
    """A fake Tibia: the window shows once started, closes on alt+f4."""

    def __init__(self) -> None:
        self.window = True
        self.started: list[Path] = []
        self.focused = 0

    def start(self, exe: Path) -> None:
        self.started.append(exe)
        self.window = True

    def focus(self) -> bool:
        self.focused += 1
        return True


def accounts_file(tmp_path: Path, text: str = "Abc1 Pw9\n\n# note\nd_e-f@x.io XY\n") -> Path:
    path = tmp_path / "accounts.txt"
    path.write_text(text, encoding="utf-8")
    return path


def setup(
    tmp_path: Path, stamina: int | None, restart: bool = True
) -> tuple[ScriptRunner, Rotation, Clock, Client]:
    clock, client = Clock(), Client()
    r = ScriptRunner(tmp_path, clock=clock)
    r.stamina = lambda: stamina
    config = RotationConfig(accounts_file=accounts_file(tmp_path), client_exe=EXE)
    r.rotation = Rotation(
        config,
        clock=clock,
        start_client=client.start,
        window_open=lambda: client.window,
        focus_client=client.focus,
        restart_client=restart,
    )
    return r, r.rotation, clock, client


def play(
    r: ScriptRunner,
    inp: object,
    clock: Clock,
    in_game: bool,
    seconds: float,
    client: Client | None = None,
) -> None:
    """Step the runner for *seconds* at 10 ticks/s, the game shown or not.

    The fake client closes when alt+f4 was sent (the keys go to the fake input).
    """
    snap = WorldState(screen_ok=in_game, hp_percent=100.0 if in_game else 0.0)
    for _ in range(int(seconds * 10)):
        r.step(snap, inp)  # type: ignore[arg-type]
        if client is not None and "K alt+f4" in getattr(inp, "sent_lines", []):
            client.window = bool(client.started)
        clock.now += 0.1




def test_load_accounts(tmp_path: Path) -> None:
    expected = (Account("Abc1", "Pw9"), Account("d_e-f@x.io", "XY"))
    assert load_accounts(accounts_file(tmp_path)) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("a1 p1\nb2\n", "line 2"),  # a bad line
        ("a1 p1 extra\nb2 p2\n", "line 1"),
        ("a1 p1\nb2 pż2\n", "password must be letters, digits and symbols"),  # not on the keyboard
        ("aż1 p1\nb2 p2\n", "login must be letters, digits and symbols"),
        ("a1 p1\n# only one\n", "at least 2 account"),
    ],
)
def test_a_bad_accounts_file_is_refused_without_the_password(
    tmp_path: Path, text: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message) as error:
        load_accounts(accounts_file(tmp_path, text))
    assert "pż2" not in str(error.value)


def test_a_missing_accounts_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="accounts file"):
        load_accounts(tmp_path / "nope.txt")


def test_validate() -> None:
    RotationConfig(accounts_file=EXE).validate()
    with pytest.raises(ValueError):
        RotationConfig(accounts_file=EXE, logout_hotkey="").validate()


def test_the_trigger_is_stamina_under_39_30() -> None:
    assert stamina_is_low(LOW_STAMINA_MINUTES - 1)
    assert not stamina_is_low(LOW_STAMINA_MINUTES) and not stamina_is_low(None)


def test_refill_is_asked_once_per_account_when_the_last_read_is_low(tmp_path: Path) -> None:
    _, rot, _, _ = setup(tmp_path, 100)
    assert not rot.refill_due(None) and not rot.refill_due(LOW_STAMINA_MINUTES)
    assert rot.refill_due(100) and not rot.refill_due(100)  # asked once


@pytest.mark.parametrize("stamina", [None, LOW_STAMINA_MINUTES])
def test_rotate_does_nothing_when_stamina_is_not_low(tmp_path: Path, stamina: int | None) -> None:
    r, _, clock, client = setup(tmp_path, stamina)
    inp = FakeInput()
    r.start("account_rotate")
    play(r, inp, clock, True, 1, client)
    assert inp.sent_lines == [] and r.running is None and not client.started


def test_low_stamina_restarts_tibia_and_logs_the_next_account_in(tmp_path: Path) -> None:
    r, rot, clock, client = setup(tmp_path, 100)
    inp = FakeInput()
    r.start("account_rotate")
    play(r, inp, clock, True, 0.5, client)
    assert inp.sent_lines == ["K ctrl+l"]
    play(r, inp, clock, False, 15, client)  # alt+f4, the window goes, started, the login typed
    assert client.started == [EXE] and client.focused == 1 and not rot.client_closed
    assert inp.sent_lines == ["K ctrl+l", "K alt+f4", *LOGIN_B, *PICK_1]
    play(r, inp, clock, True, 1, client)
    assert r.running is None and rot.index == 1 and not rot.failed


def test_a_failed_login_is_skipped_and_all_failing_stops(tmp_path: Path) -> None:
    r, rot, clock, client = setup(tmp_path, 100)
    inp = FakeInput()
    r.start("account_rotate")
    play(r, inp, clock, True, 0.2, client)
    play(r, inp, clock, False, 90, client)  # B, then A, never log in
    assert inp.sent_lines.count("K esc") == 2 and inp.sent_lines.count("K ctrl+l") == 1
    assert rot.failed and r.running is None and rot.index == 0


def test_the_client_that_never_shows_stops_the_bot(tmp_path: Path) -> None:
    r, rot, clock, client = setup(tmp_path, 100)
    client.start = lambda exe: None  # type: ignore[method-assign]
    rot._start_client = lambda exe: None
    inp = FakeInput()
    r.start("account_rotate")
    play(r, inp, clock, True, 0.2, client)
    play(r, inp, clock, False, 120, client)
    assert rot.failed and not rot.client_closed and r.running is None


def test_still_in_game_after_logout_keeps_the_account(tmp_path: Path) -> None:
    r, rot, clock, client = setup(tmp_path, 100)
    play(r, FakeInput(), clock, True, 0.1)
    r.start("account_rotate")
    play(r, FakeInput(), clock, True, 12)
    assert r.running is None and rot.index == 0 and not client.started


def test_the_login_and_password_never_reach_the_command_log(tmp_path: Path) -> None:
    r, _, clock, client = setup(tmp_path, 100)
    lines: list[str] = []
    r.start("account_rotate")
    play(r, LoggedInput(FakeInput(), lines.append), clock, True, 0.2)
    play(r, LoggedInput(FakeInput(), lines.append), clock, False, 25, client)
    assert "input: press d" not in lines and "input: press shift+x" not in lines
    assert lines.count("input: press (hidden)") == 12  # 10 login keys, 2 password keys


def test_rotate_switched_off_does_nothing_and_is_not_broken(tmp_path: Path) -> None:
    r, _, clock, client = setup(tmp_path, 100)  # low stamina
    r.rotation = None  # the Scripts tab switch is off
    inp = FakeInput()
    assert not r.start("account_rotate")
    play(r, inp, clock, True, 1, client)
    assert inp.sent_lines == [] and r.running is None and not client.started
    assert "account_rotate" not in r.failed


def test_the_queue_goes_on_after_a_restart_and_wraps_to_the_first(tmp_path: Path) -> None:
    from moonlight.scripts.rotation import load_playing

    r, rot, clock, client = setup(tmp_path, 100)
    assert rot.index == 0  # nothing saved: the first account
    inp = FakeInput()
    r.start("account_rotate")
    play(r, inp, clock, True, 0.5, client)
    play(r, inp, clock, False, 15, client)
    play(r, inp, clock, True, 1, client)
    assert rot.index == 1 and load_playing(2) == 1  # saved: the logged-out one is at the back
    _, again, _, _ = setup(tmp_path, 100)
    assert again.index == 1  # a restart goes on from it
    again._logged_in(0)  # the last account rotated: the first again
    assert load_playing(2) == 0


def test_on_linux_the_next_account_logs_in_on_the_login_screen(tmp_path: Path) -> None:
    # The logout leaves the client on its login screen; Alt+F4 never closed it.
    r, rot, clock, client = setup(tmp_path, 100, restart=False)
    inp = FakeInput()
    r.start("account_rotate")
    play(r, inp, clock, True, 0.5, client)
    play(r, inp, clock, False, 15, client)
    assert client.started == [] and client.focused == 1
    assert inp.sent_lines == ["K ctrl+l", *LOGIN_B, *PICK_1]
    play(r, inp, clock, True, 1, client)
    assert r.running is None and rot.index == 1 and not rot.failed
