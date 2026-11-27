"""The built-in script ``cavebot_rotate``: run the profile that fits the character's level.

Only profiles with Cavebot Rotate ticked (``rotate``) are picked, and only
when their level range (``min_level``..``max_level``, both included; 0 and 0
means none) holds the level. In a route the script stands after ``deposit``
and ``script account_rotate``, so it runs after every login too::

    deposit, script account_rotate, script cavebot_rotate, label Depo_Break

The level comes from the Skills window. While the running profile is ticked
and fits it stays. Otherwise a random ticked profile that fits is picked: the whole
profile (route, healing, targeting, ...) switches, and the bot goes on from
the new route's nearest waypoint. No other profile that qualifies, or an
unread level, keeps the running one. Every run logs its result and why.

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

__all__ = ["decide", "fitting_profiles", "pick_profile", "run"]

log = logging.getLogger(__name__)


def _why_not(cfg: BotConfig, level: int) -> str | None:
    """Why *cfg* cannot be picked at *level*; None when it can."""
    if not cfg.rotate:
        return "Cavebot Rotate unticked"
    if not cfg.max_level:
        return "no level range"
    if not cfg.fits_level(level):
        return f"level {level} outside {cfg.min_level}..{cfg.max_level}"
    return None


def _profiles(profiles_dir: Path) -> dict[str, BotConfig]:
    found = {}
    for path in sorted(profiles_dir.glob("*.json")):
        try:
            found[path.stem] = BotConfig.load(path)
        except ConfigError as exc:
            log.warning("cavebot rotate: profile %s skipped: %s", path.stem, exc)
    return found


def fitting_profiles(level: int, profiles_dir: Path = PROFILES_DIR) -> list[str]:
    """Names of the ticked profiles whose level range holds *level*, sorted."""
    return [n for n, cfg in _profiles(profiles_dir).items() if _why_not(cfg, level) is None]


def decide(
    level: int,
    current: str,
    profiles_dir: Path = PROFILES_DIR,
    choose: Callable[[list[str]], str] = random.choice,
) -> tuple[str | None, str]:
    """The profile to run at *level* and why.

    *current* while it is ticked and fits, else a ticked one that fits;
    None when there is none (the running one stays).
    """
    profiles = _profiles(profiles_dir)
    fitting = [n for n, cfg in profiles.items() if _why_not(cfg, level) is None]
    held = profiles.get(current)
    why = "profile file not found" if held is None else _why_not(held, level)
    if why is None:
        return current, "ticked and fits"
    if not fitting:
        return None, f"{why}, but no other ticked profile fits"
    return choose(fitting), why


def pick_profile(
    level: int,
    current: str,
    profiles_dir: Path = PROFILES_DIR,
    choose: Callable[[list[str]], str] = random.choice,
) -> str | None:
    """The profile to run at *level*: *current* while it qualifies, else one that does.

    None when no profile qualifies (the running one stays).
    """
    return decide(level, current, profiles_dir, choose)[0]


def _switch(runner: ScriptRunner, choose: Callable[[list[str]], str]) -> None:
    if runner.level is None or runner.profile_name is None or runner.switch_profile is None:
        log.warning("cavebot rotate: not switched, needs a screen capture and the GUI")
        return
    level = runner.level()
    current = runner.profile_name()
    if level is None:
        log.warning("cavebot rotate: %s kept, level not read (open the Skills window)", current)
        return
    name, why = decide(level, current, runner.profiles_dir, choose)
    if name is None:
        log.warning("cavebot rotate: level %d, %s kept: %s", level, current, why)
    elif name == current:
        log.info("cavebot rotate: level %d, %s kept: %s", level, name, why)
    else:
        log.info("cavebot rotate: level %d, %s -> %s: %s", level, current, name, why)
        runner.switch_profile(name)


def run(
    runner: ScriptRunner, choose: Callable[[list[str]], str] = random.choice
) -> Generator[object, None, None]:
    """The ``cavebot_rotate`` script (a generator without waits, like every script)."""
    _switch(runner, choose)
    yield from ()
