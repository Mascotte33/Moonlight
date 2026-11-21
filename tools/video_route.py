#!/usr/bin/env python3
"""Turn a hunting video into route lines for ``tools/route.py new``.

Usage:
    python tools/video_route.py hunt.mp4 > hunt.txt
    python tools/video_route.py hunt.mp4 --every 0.5 --every-sqm 8 --minimap 1754,30,106,109
    python tools/route.py new MyHunt < hunt.txt

The video must show the client's minimap at its real size (--zoom: px per
sqm, default 2; a 1080p recording of a full-screen client). Each sampled
frame is matched against the world map; the output is a walk every N sqm,
the floor changes (kind marked "(guess)" when no saved route uses that sqm)
and ``#`` notes with the video time for stops, jumps and unknown floor changes.
Read the notes, add the actions (refill block, labels) and load the route.

# doc: docs/prs/video-route.md#trail
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

from moonlight.calibration.autofind import find_minimap
from moonlight.cavebot.route_plan import load_floor_map, route_places
from moonlight.cavebot.video_route import Sample, TrailFinder, clean_trail, trail_lines
from moonlight.core.wiring import MAP_DIR
from moonlight.gui.loop_factory import load_real_config
from moonlight.perception.worldmap import WorldMap


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("video", type=Path)
    parser.add_argument("--every", type=float, default=0.5, help="seconds between frames")
    parser.add_argument("--every-sqm", type=int, default=8, help="sqm between walk lines")
    parser.add_argument("--minimap", help="x,y,w,h of the minimap in the video (else found)")
    parser.add_argument(
        "--zoom", type=int, default=2, help="minimap px per sqm (compression hides it)"
    )
    args = parser.parse_args()

    video = cv2.VideoCapture(str(args.video))
    if not video.isOpened():
        sys.exit(f"cannot open {args.video}")
    fps = video.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(fps * args.every))
    region = tuple(int(v) for v in args.minimap.split(",")) if args.minimap else None
    finder = TrailFinder(WorldMap(MAP_DIR, zooms=(args.zoom,), full_search_every=0))
    samples: list[Sample] = []
    lost = 0
    frame_number = 0
    while True:
        ok, bgr = video.read()
        if not ok:
            break
        if frame_number % step == 0:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            if region is None:
                region = find_minimap(rgb)
            if region is not None:
                x, y, w, h = region
                spot = finder.find(rgb[y : y + h, x : x + w])
                if spot is None:
                    lost += 1
                else:
                    samples.append(Sample(frame_number / fps, spot))
        frame_number += 1
    if region is None:
        sys.exit("no minimap found in the video: pass --minimap x,y,w,h")
    trail = clean_trail(samples)
    print(
        f"# {args.video.name}: {len(samples)} frames placed, {lost} not, "
        f"{len(samples) - len(trail)} dropped as bad matches"
    )
    routes_dir = load_real_config().routes_dir
    floors = load_floor_map(MAP_DIR, routes_dir)
    for line in trail_lines(trail, floors, route_places(routes_dir), every_sqm=args.every_sqm):
        print(line)


if __name__ == "__main__":
    main()
