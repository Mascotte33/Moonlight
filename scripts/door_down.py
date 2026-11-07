"""Open the door down of the player: add a 'script' waypoint with label 'door_down'
on the tile in front of the door. Right-clicks the door, then waits 0.5 s.
"""

from collections.abc import Iterator

from moonlight.scripts.scripts import Bot


def run(bot: Bot) -> Iterator[object]:
    yield bot.use_tile_next_to_player("down")
    yield 0.5
