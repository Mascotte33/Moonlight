"""The built-in script ``cavebot_rotate``: run the profile that fits the character's level.

Each profile may have a level range (``min_level``..``max_level``, both
included; 0 and 0 means none). In a route the script stands after ``deposit``
and ``script account_rotate``, so it runs after every login too::

    deposit, script account_rotate, script cavebot_rotate, label Depo_Break

The level comes from the Skills window. While the running profile fits the
level it stays. Otherwise a random profile that fits is picked: the whole
profile (route, healing, targeting, ...) switches, and the bot goes on from
the new route's nearest waypoint. No profile that fits, or an unread level,
keeps the running one (logged).

# doc: docs/prs/profiles.md#level-pick
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Generator
from pathlib import Path
from typing import TYPE_CHECKING

from moonlight.core.config import PROFILES_DIR, BotConfig, ConfigError

if TYPE_CHECKING:
    from moonlight.scripts.scripts import ScriptRunner

__all__ = ["fitting_profiles", "pick_profile", "run"]

log = logging.getLogger(__name__)


def fitting_profiles(level: int, profiles_dir: Path = PROFILES_DIR) -> list[str]:
    """Names of the profiles whose level range holds *level*, sorted."""
    names = []
    for path in sorted(profiles_dir.glob("*.json")):
        try:
            if BotConfig.load(path).fits_level(level):
                names.append(path.stem)
        except ConfigError as exc:
            log.warning("cavebot rotate: profile %s skipped: %s", path.stem, exc)
    return names


def pick_profile(
    level: int,
    current: str,
    profiles_dir: Path = PROFILES_DIR,
    choose: Callable[[list[str]], str] = random.choice,
) -> str | None:
    """The profile to run at *level*: *current* while it fits, else one that does.

    None when no profile fits (the running one stays).
    """
    fitting = fitting_profiles(level, profiles_dir)
    if current in fitting:
        return current
    return choose(fitting) if fitting else None


def _switch(runner: ScriptRunner, choose: Callable[[list[str]], str]) -> None:
    if runner.level is None or runner.profile_name is None or runner.switch_profile is None:
        log.warning("cavebot rotate: needs a screen capture and the GUI, skipped")
        return
    level = runner.level()
    if level is None:
        log.warning("cavebot rotate: level not read (open the Skills window), profile kept")
        return
    current = runner.profile_name()
    name = pick_profile(level, current, runner.profiles_dir, choose)
    if name is None:
        log.warning("cavebot rotate: no profile fits level %d, %s kept", level, current)
    elif name == current:
        log.info("cavebot rotate: level %d -> %s (the running profile)", level, name)
    else:
        log.info("cavebot rotate: level %d -> profile %s", level, name)
        runner.switch_profile(name)


def run(
    runner: ScriptRunner, choose: Callable[[list[str]], str] = random.choice
) -> Generator[object, None, None]:
    """The ``cavebot_rotate`` script (a generator without waits, like every script)."""
    _switch(runner, choose)
    yield from ()
