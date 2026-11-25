"""Account rotation: log the next account in when the stamina is low.

The accounts are lines ``login password`` of a text file the owner picks
(blank lines and ``#`` lines are ignored); the bot assumes the first line is
the account playing when it starts. When the last stamina read is under
:data:`~moonlight.perception.stamina.LOW_STAMINA_MINUTES` the cavebot's
refill flag is set once, so the next ``check`` waypoint goes to town; the
``script`` waypoint ``account_rotate`` at the depot reads the stamina again and, when
it is low, logs out, closes the Tibia client, starts it again and logs the
next account's first character in. Stamina not low (or unread), ``account_rotate``
does nothing.

The login and password are typed (letters, digits and ``@ . - _``: the
Arduino's keys on a US layout), both kept out of the log. An account that
does not log in within :data:`LOGIN_WAIT` s is logged and skipped; when
every account fails the bot alarms and stops.

# doc: docs/prs/stamina-switch.md#stamina-switch
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from moonlight.healing.healing import check_hotkey
from moonlight.perception.capture import _find_window_hwnd, focus_window
from moonlight.perception.stamina import LOW_STAMINA_MINUTES
from moonlight.scripts.scripts import TYPEABLE

if TYPE_CHECKING:
    from moonlight.scripts.scripts import Bot

__all__ = ["Account", "Rotation", "RotationConfig", "load_accounts", "stamina_is_low"]

log = logging.getLogger(__name__)

LOGOUT_WAIT = 10.0  # s for the game to go after the logout key; still there = stay
CLOSE_WAIT = 15.0  # s for the client window to go after alt+f4
START_WAIT = 90.0  # s for the client window to show after starting the program
LOGIN_SCREEN_WAIT = 5.0  # s for the login screen to be ready after the window shows
LOGIN_WAIT = 30.0  # s for the game to show after Enter on the character
GONE_FOR = 2.0  # s the game must stay gone: one bad frame is not a logout
LIST_TOP = 20  # ponytail: "up" presses to reach the list top (= the first character)
#: The official client install: the Windows launcher, or the Linux one in ~/Tibia.
DEFAULT_CLIENT_EXE = (
    Path.home() / "Tibia" / "Tibia"
    if sys.platform == "linux"
    else Path(os.environ.get("LOCALAPPDATA", "")) / "Tibia" / "packages" / "Tibia" / "bin"
    / "client_launcher.exe"
)


@dataclass(frozen=True)
class Account:
    login: str
    password: str


@dataclass(frozen=True)
class RotationConfig:
    #: Text file, one ``login password`` per line; None = rotation is off.
    accounts_file: Path | None = None
    client_exe: Path = DEFAULT_CLIENT_EXE
    logout_hotkey: str = "ctrl+l"

    def validate(self) -> RotationConfig:
        check_hotkey(self.logout_hotkey, "rotation.logout_hotkey")
        return self


def stamina_is_low(minutes: int | None) -> bool:
    """Under 39:30; an unread stamina (None) is never low."""
    return minutes is not None and minutes < LOW_STAMINA_MINUTES


def load_accounts(path: Path, at_least: int = 2) -> tuple[Account, ...]:
    """The accounts in *path*; the password is never put into an error.

    Raises:
        ValueError: For a missing file, a bad line, a login or password that
            :meth:`Bot.type` can't type, or fewer than 2 accounts.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"rotation accounts file {path}: {exc.strerror or exc}") from exc
    accounts = []
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        if len(words) != 2:
            raise ValueError(f"rotation accounts file line {number}: expected 'login password'")
        for name, word in zip(("login", "password"), words, strict=True):
            if not word or not re.fullmatch(TYPEABLE, word):
                raise ValueError(
                    f"rotation accounts file line {number}: "
                    f"{name} must be letters, digits and symbols (no spaces)"
                )
        accounts.append(Account(*words))
    if len(accounts) < at_least:
        raise ValueError(f"the accounts file needs at least {at_least} account(s)")
    return tuple(accounts)


class Rotation:
    """Which account plays and the switch to the next one (which restarts Tibia)."""

    def __init__(
        self,
        config: RotationConfig,
        clock: Callable[[], float] = time.monotonic,
        *,
        window_title: str = "Tibia",
        start_client: Callable[[Path], object] | None = None,
        window_open: Callable[[], bool] | None = None,
        focus_client: Callable[[], bool] | None = None,
    ) -> None:
        if config.accounts_file is None:
            raise ValueError("rotation needs an accounts file")
        self.config = config
        self.accounts = load_accounts(config.accounts_file)
        self._clock = clock
        self._start_client = start_client or (lambda exe: subprocess.Popen([str(exe)]))
        self._window_open = window_open or (lambda: _find_window_hwnd(window_title) is not None)
        self._focus_client = focus_client or (lambda: focus_window(window_title))
        self.index = 0
        self._asked = False
        #: No client window while the switch restarts Tibia: the loop steps the
        #: script without the focus gate then (nothing is typed meanwhile).
        self.client_closed = False
        #: No account logged in or the client would not restart: the loop alarms and stops.
        self.failed = False

    def refill_due(self, last_stamina_minutes: int | None) -> bool:
        """True once per account when the last stamina read is low: send the character to town."""
        if self._asked or not stamina_is_low(last_stamina_minutes):
            return False
        self._asked = True
        log.info("rotation: stamina %s min is low, going to the depot", last_stamina_minutes)
        return True

    def run(self, bot: Bot) -> Generator[object, None, None]:
        """The ``account_rotate`` script: log out, restart Tibia, log the next in."""
        if not stamina_is_low(bot.stamina()):
            return
        yield bot.press(self.config.logout_hotkey)
        if not (yield from self._wait(bot, in_game=False, limit=LOGOUT_WAIT)):
            log.warning("rotation: still in game after logout, staying on this account")
            self._logged_in(self.index)
            return
        yield bot.press("alt+f4")
        self.client_closed = True
        if not (yield from self._wait_for_window(present=False, limit=CLOSE_WAIT)):
            self._fail("the client did not close after alt+f4")
            return
        self._start_client(self.config.client_exe)
        if not (yield from self._wait_for_window(present=True, limit=START_WAIT)):
            self._fail("the client window did not show after starting it")
            return
        self._focus_client()
        self.client_closed = False
        yield LOGIN_SCREEN_WAIT
        n = len(self.accounts)
        for step in range(1, n + 1):
            nxt = (self.index + step) % n
            yield from self._login(bot, self.accounts[nxt])
            if (yield from self._wait(bot, in_game=True, limit=LOGIN_WAIT)):
                # doc: docs/prs/profiles.md#level-pick
                # cavebot_rotate (next in the route) picks the profile by level.
                self._logged_in(nxt)
                return
            log.warning("rotation: account %d did not log in, skipped", nxt + 1)
            yield bot.press("esc")  # close the error message
            yield 1
        self._fail("no account logged in")

    def _fail(self, reason: str) -> None:
        log.error("rotation: %s", reason)
        self.client_closed = False
        self.failed = True

    def _logged_in(self, index: int) -> None:
        self.index, self._asked = index, False
        log.info("rotation: account %d playing", index + 1)

    def _login(self, bot: Bot, acc: Account) -> Generator[object, None, None]:
        yield [*bot.press("ctrl+a"), *bot.type(acc.login, secret=True), *bot.press("tab")]
        yield [*bot.press("ctrl+a"), *bot.type(acc.password, secret=True), *bot.press("enter")]
        yield 3  # the character list opens
        yield [c for k in ["up"] * LIST_TOP + ["enter"] for c in bot.press(k)]  # the first one

    def _wait_for_window(self, *, present: bool, limit: float) -> Generator[object, None, bool]:
        """Wait until the client window shows (or is gone); False after *limit*."""
        end = self._clock() + limit
        while self._clock() < end:
            if self._window_open() == present:
                return True
            yield 0.5
        return False

    def _wait(self, bot: Bot, *, in_game: bool, limit: float) -> Generator[object, None, bool]:
        """Wait until the game shows (or is gone for :data:`GONE_FOR` s); False after *limit*."""
        end = self._clock() + limit
        since: float | None = None
        while self._clock() < end:
            if bot.in_game == in_game:
                if in_game:
                    return True
                since = self._clock() if since is None else since
                if self._clock() - since >= GONE_FOR:
                    return True
            else:
                since = None
            yield 0.2
        return False


# doc: docs/prs/stamina-switch.md#login-on-start
def login_on_start(
    config: RotationConfig,
    *,
    window_open: Callable[[], bool],
    start_client: Callable[[Path], object],
    focus_client: Callable[[], bool],
    in_game: Callable[[], bool],
    press: Callable[[str], None],
    quiet: Callable[[bool], None],
    log_info: Callable[[str], None],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Before Start calibrates: open Tibia and log the first account in.

    Does nothing while the game already shows. Without a client window it
    starts ``client_exe``; on the login screen it types the first account's
    login and password (the command log muted) and enters its first
    character, then waits for the game.

    Raises:
        RuntimeError: When the client does not start or the game does not show.
    """
    from moonlight.core.random_delay import typing_gap
    from moonlight.scripts.scripts import type_keys

    if window_open() and in_game():
        return
    if config.accounts_file is None:
        return
    account = load_accounts(config.accounts_file, at_least=1)[0]
    if not window_open():
        log_info("login: starting Tibia")
        start_client(config.client_exe)
        end = clock() + START_WAIT
        while not window_open():
            if clock() > end:
                raise RuntimeError("login: the Tibia window did not show")
            sleep(0.5)
        sleep(LOGIN_SCREEN_WAIT)
    focus_client()
    sleep(1.0)
    if in_game():
        return
    log_info("login: logging account 1 in")

    def keys(combos: list[str]) -> None:
        for combo in combos:
            press(combo)
            sleep(typing_gap())

    quiet(True)
    try:
        keys(["ctrl+a", *type_keys(account.login), "tab"])
        keys(["ctrl+a", *type_keys(account.password), "enter"])
    finally:
        quiet(False)
    sleep(3.0)  # the character list opens
    keys(["up"] * LIST_TOP + ["enter"])  # the first character
    end = clock() + LOGIN_WAIT
    while not in_game():
        if clock() > end:
            raise RuntimeError("login: the game did not show after logging in")
        sleep(0.5)
    log_info("login: in game")
    sleep(2.0)  # the side panel draws
