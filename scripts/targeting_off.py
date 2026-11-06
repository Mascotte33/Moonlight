"""Turn targeting off: the bot picks no new monster until targeting_on runs.

Add a 'script' waypoint with label 'targeting_off'. bot.log shows
``script: targeting off``. Healing and the cavebot go on.
"""

from collections.abc import Iterator

from moonlight.scripts.scripts import Bot


def run(bot: Bot) -> Iterator[object]:
    yield bot.targeting(False)
