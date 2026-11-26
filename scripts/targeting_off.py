"""Turn targeting off: the bot picks no new monster until targeting_on runs.

Add a 'script' waypoint with label 'targeting_off'. bot.log shows
``script: targeting off``. Healing and the cavebot go on: the bot walks past
monsters (a selected one too) and walks a long way round them; it fights only
when creatures leave no way past. Use it off the hunt (Point 0, the trip to
town, refill, the way back) and ``targeting_on`` at ``label StartHunt``.
"""

from collections.abc import Iterator

from moonlight.scripts.scripts import Bot


def run(bot: Bot) -> Iterator[object]:
    yield bot.targeting(False)
