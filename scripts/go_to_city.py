"""Point 0: jump the route to label ``From<City>`` of the city depot you stand in.

The route's first waypoint is ``script go_to_city``; ``python tools/route.py
block travel --depot x,y,z`` prints it with one ``From<City>`` block per city
that walks to the route's depot. Standing in no known depot fails the script
(bot.log names the position) and the route goes on to its own city's block.
"""

from collections.abc import Iterator

from moonlight.cavebot.route_plan import nearest_city
from moonlight.scripts.scripts import Bot


def run(bot: Bot) -> Iterator[object]:
    city = nearest_city(*bot.position)
    if city is None:
        raise RuntimeError(f"not in a known city depot at {bot.position}")
    yield bot.goto(f"From{city}")
