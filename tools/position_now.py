#!/usr/bin/env python3
"""Print the position the bot reads from the open client, a few times a second.

Usage:
    python tools/position_now.py        # 20 reads
    python tools/position_now.py 100

Each line: time, x/y/z, where the position came from, and the map match score.
Uses the real config's profile and map, like the bot.
"""

from __future__ import annotations

import argparse
import time

from moonlight.core.wiring import MAPS_DIR, _world_map
from moonlight.gui.loop_factory import load_profile, load_real_config
from moonlight.perception.capture import ScreenCapture
from moonlight.perception.floor import FloorReader
from moonlight.perception.minimap import MinimapReader


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reads", type=int, nargs="?", default=20)
    reads = parser.parse_args().reads
    config = load_real_config()
    profile = load_profile(config.profile, config.profiles_dir)
    px_per_sqm = max(1, round(profile.ui_scale))
    world_map = _world_map(MAPS_DIR / config.game_map, (px_per_sqm, 2 * px_per_sqm))
    minimap = MinimapReader(
        profile, floor_reader=FloorReader(profile), map_matcher=world_map, px_per_sqm=px_per_sqm
    )
    capture = ScreenCapture(backend="mss")
    for _ in range(reads):
        position = minimap.read_position(capture.get_frame())
        print(time.strftime("%X"), position, minimap.source, round(world_map.score, 3))
        time.sleep(0.3)


if __name__ == "__main__":
    main()
