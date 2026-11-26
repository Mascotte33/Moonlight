"""Point 0: jump the route to label ``From<City>`` of the city depot you stand in.

The route's first waypoint is ``script go_to_city``; ``python tools/route.py
block travel --depot x,y,z`` prints it with one ``From<City>`` block per city
that walks to the route's depot. Standing in no known depot fails the script
(bot.log names the position) and the route goes on to its own city's block.
On another floor of the depot (Thais depot -1 is floor 8) it jumps to
``From<City>Floor<z>`` when the route has it: the way to the depot's own
floor, ending in ``goto From<City>``.
"""

from collections.abc import Iterator

from moonlight.cavebot.route_plan import CITY_DEPOTS, nearest_city
from moonlight.scripts.scripts import Bot


def run(bot: Bot) -> Iterator[object]:
    city = nearest_city(*bot.position)
    if city is None:
        raise RuntimeError(f"not in a known city depot at {bot.position}")
    z = bot.position[2]
    floor_block = f"From{city}Floor{z}"
    if z != CITY_DEPOTS[city][2] and bot.has_label(floor_block):
        yield bot.goto(floor_block)  # another depot floor: its own way to the city block
    else:
        yield bot.goto(f"From{city}")
