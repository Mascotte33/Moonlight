"""Account rotation: play accounts A, B, C, A... in one client window.

Each account plays for its own minutes, clock time from its login (the
bot's start counts as the first account's login). When the time is up the
cavebot's refill flag is set once, so the next ``check`` waypoint goes to
town; the ``script`` waypoint ``rotate`` at the depot then logs out and logs
the next account in. Not due yet, ``rotate`` does nothing.

The switch types the account name and password (letters and digits only:
the Arduino has no symbol keys) and picks the character by its place in
the character list. An account that does not log in within
:data:`LOGIN_WAIT` s is logged and skipped; when every account fails the
bot alarms and stops. Passwords never reach a log.

# doc: docs/prs/rotation.md#rotation
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from moonlight.healing.healing import check_hotkey

if TYPE_CHECKING:
    from moonlight.scripts.scripts import Bot

__all__ = ["Account", "Rotation", "RotationConfig"]

log = logging.getLogger(__name__)

LOGOUT_WAIT = 10.0  # s for the game to go after the logout key; still there = stay
LOGIN_WAIT = 30.0  # s for the game to show after Enter on the character
GONE_FOR = 2.0  # s the game must stay gone: one bad frame is not a logout
LIST_TOP = 20  # ponytail: "up" presses to reach the list top, raise for longer lists


@dataclass(frozen=True)
class Account:
    login: str
    password: str
    #: The character's place in the character list, 1 = the first.
    character: int = 1
    minutes: float = 60.0


@dataclass(frozen=True)
class RotationConfig:
    accounts: tuple[Account, ...] = ()
    logout_hotkey: str = "ctrl+l"

    def validate(self) -> RotationConfig:
        check_hotkey(self.logout_hotkey, "rotation.logout_hotkey")
        for i, acc in enumerate(self.accounts, 1):
            for name in ("login", "password"):
                if not re.fullmatch(r"[A-Za-z0-9]+", getattr(acc, name)):
                    raise ValueError(f"rotation account {i}: {name} must be letters and digits")
            if acc.character < 1:
                raise ValueError(f"rotation account {i}: character must be >= 1")
            if acc.minutes <= 0:
                raise ValueError(f"rotation account {i}: minutes must be > 0")
        return self


class Rotation:
    """Which account plays, since when, and the switch to the next one."""

    def __init__(self, config: RotationConfig, clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self._clock = clock
        self.index = 0
        self.logged_in_at = clock()
        self._asked = False
        #: Every account failed to log in: the loop alarms and stops.
        self.failed = False

    @property
    def account(self) -> Account:
        return self.config.accounts[self.index]

    def due(self) -> bool:
        return self._clock() - self.logged_in_at >= self.account.minutes * 60

    def refill_due(self) -> bool:
        """True once per account when its time is up: send the character to town."""
        if self._asked or not self.due():
            return False
        self._asked = True
        log.info("rotation: account %d time is up, going to the depot", self.index + 1)
        return True

    def run(self, bot: Bot) -> Generator[object, None, None]:
        """The ``rotate`` script: log out, then log the next account in."""
        if not self.due():
            return
        yield bot.press(self.config.logout_hotkey)
        if not (yield from self._wait(bot, in_game=False, limit=LOGOUT_WAIT)):
            log.warning("rotation: still in game after logout, staying on this account")
            self._logged_in(self.index)
            return
        n = len(self.config.accounts)
        for step in range(1, n + 1):
            nxt = (self.index + step) % n
            yield from self._login(bot, self.config.accounts[nxt])
            if (yield from self._wait(bot, in_game=True, limit=LOGIN_WAIT)):
                self._logged_in(nxt)
                return
            log.warning("rotation: account %d did not log in, skipped", nxt + 1)
            yield bot.press("esc")  # close the error message
            yield 1
        log.error("rotation: no account logged in")
        self.failed = True

    def _logged_in(self, index: int) -> None:
        self.index, self.logged_in_at, self._asked = index, self._clock(), False
        log.info("rotation: account %d playing", index + 1)

    def _login(self, bot: Bot, acc: Account) -> Generator[object, None, None]:
        yield [*bot.press("ctrl+a"), *bot.type(acc.login), *bot.press("tab")]
        yield [*bot.press("ctrl+a"), *bot.type(acc.password, secret=True), *bot.press("enter")]
        yield 3  # the character list opens
        keys = ["up"] * LIST_TOP + ["down"] * (acc.character - 1) + ["enter"]
        yield [c for k in keys for c in bot.press(k)]

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
