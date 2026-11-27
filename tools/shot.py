#!/usr/bin/env python3
"""A small screenshot of the Tibia client: the game view by default.

Usage (from the repo root):
    python tools/shot.py                    # the game view, half size
    python tools/shot.py minimap --scale 2  # the minimap, twice as big
    python tools/shot.py client --scale 0.4 # the whole client window
    python tools/shot.py battle_list --out /tmp/b.png

A part is ``client`` or a region of ``data/calibration.json`` (``game`` is
``game_grid``), placed on the client window found by its title. Only the
client in front is captured; otherwise nothing is. A full-screen
PNG is ~650 KB; the half-size game view is ~150 KB. Prints the PNG's path.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import mss
import numpy as np

from moonlight import x11

CALIBRATION = Path("data") / "calibration.json"
ALIASES = {"game": "game_grid"}


def client_rect(title: str) -> tuple[int, int, int, int]:
    """Screen ``(left, top, width, height)`` of the client window.

    Exits unless the client is the window in front: the grab copies the
    screen, so a client behind other windows would capture them instead
    (2026-11-27 it caught a browser and a text file).
    """
    wid = x11.find_window(title)
    rect = None if wid is None else x11.window_rect(wid)
    if rect is None:
        sys.exit(f"no window titled {title!r} on screen")
    front = x11.active_title() or ""
    if front != x11.title_of(wid or 0):
        sys.exit("the client is not in front: nothing captured")
    return rect


def part_rect(part: str, client: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Screen rect of *part*: the client, or a calibrated region inside it."""
    if part == "client":
        return client
    regions = json.loads(CALIBRATION.read_text())["regions"]
    name = ALIASES.get(part, part)
    if name not in regions:
        sys.exit(f"{part!r} is not a part: client, game, {', '.join(sorted(regions))}")
    x, y, w, h = regions[name]
    return client[0] + x, client[1] + y, w, h


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "part", nargs="?", default="game", help="client, game or a calibrated region"
    )
    parser.add_argument("--scale", type=float, default=0.5, help="size factor (default 0.5)")
    parser.add_argument("--out", default="logs/shot.png", help="the PNG path")
    parser.add_argument("--title", default="Tibia", help="the client window's title")
    args = parser.parse_args()
    left, top, width, height = part_rect(args.part, client_rect(args.title))
    with mss.mss() as screen:
        raw = screen.grab({"left": left, "top": top, "width": width, "height": height})
    image = np.asarray(raw)[..., :3]
    if args.scale != 1.0:
        size = (max(1, round(width * args.scale)), max(1, round(height * args.scale)))
        shrink = cv2.INTER_AREA if args.scale < 1 else cv2.INTER_NEAREST
        image = cv2.resize(image, size, interpolation=shrink)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(args.out, image)
    print(
        f"{args.out}: {image.shape[1]}x{image.shape[0]}, {Path(args.out).stat().st_size // 1024} KB"
    )


if __name__ == "__main__":
    main()
