"""Start logs the first account in (docs/prs/stamina-switch.md#login-on-start)."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonlight.scripts.rotation import RotationConfig, login_on_start


def _run(
    tmp_path: Path, *, window: bool, game_after: int
) -> tuple[list[str], list[bool], list[str]]:
    accounts = tmp_path / "accounts.txt"
    accounts.write_text("me@x.io ?T5]#\n", encoding="utf-8")
    pressed: list[str] = []
    muted: list[bool] = []
    started: list[str] = []
    state = {"window": window, "checks": 0}
    now = [0.0]

    def in_game() -> bool:
        state["checks"] += 1
        return state["checks"] > game_after

    def start(exe: Path) -> None:
        started.append(str(exe))
        state["window"] = True

    def sleep(s: float) -> None:
        now[0] += s

    login_on_start(
        RotationConfig(accounts_file=accounts, client_exe=Path("/t/Tibia")),
        window_open=lambda: bool(state["window"]),
        start_client=start,
        focus_client=lambda: True,
        in_game=in_game,
        press=pressed.append,
        quiet=muted.append,
        log_info=lambda _m: None,
        sleep=sleep,
        clock=lambda: now[0],
    )
    return pressed, muted, started


def test_nothing_happens_while_the_game_shows(tmp_path: Path) -> None:
    assert _run(tmp_path, window=True, game_after=0) == ([], [], [])


def test_tibia_is_started_and_the_first_account_typed(tmp_path: Path) -> None:
    pressed, muted, started = _run(tmp_path, window=False, game_after=3)
    assert started == ["/t/Tibia"]
    assert pressed[:3] == ["ctrl+a", "m", "e"]
    assert "shift+slash" in pressed and "rbracket" in pressed and "shift+3" in pressed
    assert pressed[-1] == "enter" and pressed.count("up") == 20
    assert muted == [True, False]  # the login and password never reach the command log


def test_a_login_that_never_shows_the_game_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="did not show"):
        _run(tmp_path, window=True, game_after=10_000)
