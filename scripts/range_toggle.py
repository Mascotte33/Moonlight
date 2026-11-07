"""Flip Targeting's "Only monsters within N tiles" box (on -> off, off -> on).

Add a 'script' waypoint with label 'range_toggle'. bot.log shows
``script: only monsters within 3 tiles on`` (or off). For a fixed state use
``bot.only_monsters_within_range(True)`` or ``(False)`` instead of ``None``.
"""

from collections.abc import Iterator

from moonlight.scripts.scripts import Bot


def run(bot: Bot) -> Iterator[object]:
    yield bot.only_monsters_within_range(None)
