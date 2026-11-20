"""Depot break: the cavebot stands still 5-13 min.

Add a 'script' waypoint with label 'Depo_Break' (in the depot, say).
Healing, targeting and looting go on; only walking waits. bot.log shows
``script Depo_Break started`` / ``ended``.
"""

import random
from collections.abc import Iterator

from moonlight.scripts.scripts import Bot


def run(bot: Bot) -> Iterator[object]:
    yield random.uniform(5 * 60, 13 * 60)
