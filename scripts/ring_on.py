"""Turn Extras' ring on.

Add a 'script' waypoint with label 'ring_on' at the hunt start.
"""

from collections.abc import Iterator

from moonlight.scripts.scripts import Bot


def run(bot: Bot) -> Iterator[object]:
    yield bot.ring(True)
