"""Turn Extras' ring off and take the ring off (presses the ring hotkey).

Add a 'script' waypoint with label 'ring_off' where the refill trip starts.
"""

from collections.abc import Iterator

from moonlight.scripts.scripts import Bot


def run(bot: Bot) -> Iterator[object]:
    yield bot.ring(False)
