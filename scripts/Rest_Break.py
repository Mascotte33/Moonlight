"""Rest break: the cavebot stands still 10-30 s, every 30-60 min.

Tick Rest_Break on the Scripts tab (its pause box does not matter: the
script picks the next start itself). Healing, targeting and looting go on;
only walking waits. bot.log shows ``script Rest_Break started`` / ``ended``.
The first break comes right when the bot starts.
"""

import random
from collections.abc import Iterator

from moonlight.scripts.scripts import Bot


def run(bot: Bot) -> Iterator[object]:
    bot.run_again_in(random.uniform(30 * 60, 60 * 60))
    yield random.uniform(10, 30)
