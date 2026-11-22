"""The built-in script ``cavebot_rotate``: pick a route that fits the character's level.

A route file ``VenoreSwampTrolls 20-40.json`` is for levels 20 to 40, both
included; a route without such a trailing `` <min>-<max>`` is never picked. In
a route the script stands after ``deposit`` and ``script account_rotate``::

    deposit, script account_rotate, script cavebot_rotate, label Depo_Break

The level comes from the Skills window. A random route that fits is loaded and
started at waypoint 0 (it may be the current one: then the route goes on). One
flag, ``ScriptRunner.skip_cavebot_rotate``, skips the next run: the account
switch sets it (the new account keeps this route) and so does a route switch
(the new route reaches its own ``cavebot_rotate`` from waypoint 0; without the
skip it would switch again forever). An unread level or no route that fits
stops the route with an alarm instead of guessing.

# doc: docs/prs/cavebot-rotate.md#cavebot-rotate
"""

from __future__ import annotations

import logging
import random
import re
from collections.abc import Callable, Generator
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from moonlight.scripts.scripts import ScriptRunner

__all__ = ["CavebotRotateError", "pick_route", "route_level_range", "run"]

log = logging.getLogger(__name__)


class CavebotRotateError(RuntimeError):
    """The route cannot be rotated: the route stops with this as its alarm."""


def route_level_range(route_name: str) -> tuple[int, int] | None:
    """The levels ``(min, max)`` of ``"Name 20-40"``; None without a trailing `` <min>-<max>``."""
    found = re.fullmatch(r".* (\d+)-(\d+)", route_name)
    if found is None:
        return None
    low, high = int(found[1]), int(found[2])
    return (low, high) if low <= high else None


def pick_route(
    routes_dir: Path, level: int, choose: Callable[[list[str]], str] = random.choice
) -> str:
    """A route name in *routes_dir* (chosen by *choose*) whose range holds *level*.

    Raises:
        CavebotRotateError: When no route fits.
    """
    fitting = []
    for path in sorted(routes_dir.glob("*.json")):
        levels = route_level_range(path.stem)
        if levels is not None and levels[0] <= level <= levels[1]:
            fitting.append(path.stem)
    if not fitting:
        raise CavebotRotateError(f"no route in {routes_dir} fits level {level}")
    return choose(fitting)


def _switch(runner: ScriptRunner, choose: Callable[[list[str]], str]) -> None:
    from moonlight.cavebot.cavebot import RouteError, RouteFileLoader

    if runner.skip_cavebot_rotate:
        runner.skip_cavebot_rotate = False
        log.info("cavebot rotate: skipped, the account or the route was just switched")
        return
    if runner.cavebot is None or runner.routes_dir is None or runner.level is None:
        raise CavebotRotateError("cavebot rotate needs the cavebot and a screen capture")
    level = runner.level()
    if level is None:
        raise CavebotRotateError("level not read: open the Skills window")
    name = pick_route(runner.routes_dir, level, choose)
    if name == runner.cavebot.route.name:
        log.info("cavebot rotate: level %d -> %s (the current route)", level, name)
        return
    try:
        route = RouteFileLoader.load(runner.routes_dir / f"{name}.json")
        runner.cavebot.switch_route(route)
    except (RouteError, ValueError) as exc:
        raise CavebotRotateError(f"route {name!r}: {exc}") from exc
    runner.skip_cavebot_rotate = True
    log.info("cavebot rotate: level %d -> %s", level, name)


def run(
    runner: ScriptRunner, choose: Callable[[list[str]], str] = random.choice
) -> Generator[object, None, None]:
    """The ``cavebot_rotate`` script (a generator without waits, like every script)."""
    try:
        _switch(runner, choose)
    except CavebotRotateError as exc:
        log.error("cavebot rotate: %s", exc)
        if runner.cavebot is not None:
            runner.cavebot.stop_with_alarm(f"cavebot rotate: {exc}")
    yield from ()
