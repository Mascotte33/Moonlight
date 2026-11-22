"""Throw away the Cavebot tab's drop items when free capacity is under CAP_BELOW oz.

Tick it on the Scripts tab. It needs "Drop the items" ticked and the items
picked on the Cavebot tab (Settings).
"""

from collections.abc import Iterator

from moonlight.scripts.scripts import Bot

#: Free capacity (the "Cap:" number, oz) under which the items are dropped.
CAP_BELOW = 100
#: Seconds between two checks.
CHECK_EVERY = 30


def run(bot: Bot) -> Iterator[object]:
    cap = bot.free_cap
    if cap is not None and cap < CAP_BELOW:
        yield bot.drop_items()
    bot.run_again_in(CHECK_EVERY)
