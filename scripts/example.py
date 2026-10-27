"""Example script: run it from a 'script' waypoint with label 'example',
or on a timer with "example 60" in Cavebot > Scripts.

Each ``yield`` hands the tick back to the bot, so healing never waits.
"""

from collections.abc import Iterator

from moonlight.scripts.scripts import Bot


def run(bot: Bot) -> Iterator[object]:
    if bot.mana > 90:
        yield bot.say("utevo lux")  # a light spell burns spare mana
    yield 1  # wait 1 second
    yield bot.press("f11")  # bind F11 to something harmless in the client
