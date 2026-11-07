"""Turn targeting back on after targeting_off.

Add a 'script' waypoint with label 'targeting_on'. bot.log shows
``script: targeting on``.
"""

from collections.abc import Iterator

from moonlight.scripts.scripts import Bot


def run(bot: Bot) -> Iterator[object]:
    yield bot.targeting(True)
