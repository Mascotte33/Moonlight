#!/usr/bin/env python3
"""Restart the bot (running, GUI minimized) and print how its start went.

Run from the repo root. Usage:
    python tools/restart.py                # restart where the route left off
    python tools/restart.py --at 250       # start the profile's route at waypoint 250
    python tools/restart.py --watch 20     # print 20 s of lines after the start

Stops the running bot, sets the route's resume waypoint in
``data/route_progress.json`` (``--at``), starts ``start-moonlight-gui.sh
--start --minimized`` and prints the log (``tools/log.py``'s filter) until the
cavebot has loaded its route, then ``--watch`` seconds more. A failed
start (``could not start bot``, often the potions not found on the action bar)
is printed and waited through: the GUI tries again. Exits 1 when the cavebot
has not started within ``--timeout`` seconds.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

from log import wait_for  # tools/log.py

from moonlight.cavebot.cavebot import ROUTE_PROGRESS_FILE
from moonlight.gui.loop_factory import load_real_config

BOT = "^.venv/bin/python -m moonlight"
#: The cavebot's first line of a start (the route it loaded).
STARTED = re.compile(r"cavebot: route '.*', \d+ waypoints")


def stop_bot(timeout: float = 15.0) -> None:
    """Stop the running bot; exits when it is still there after *timeout* seconds."""
    subprocess.run(["pkill", "-f", BOT], check=False)
    deadline = time.monotonic() + timeout
    while subprocess.run(["pgrep", "-f", BOT], capture_output=True).returncode == 0:
        if time.monotonic() > deadline:
            sys.exit("the bot did not stop")
        time.sleep(0.5)


def set_start(index: int) -> None:
    """Make the profile's route go on from waypoint *index*."""
    route = load_real_config().cavebot.route
    if not 0 <= index < len(route.waypoints):
        sys.exit(f"--at {index}: {route.name!r} has {len(route.waypoints)} waypoints")
    progress = json.loads(ROUTE_PROGRESS_FILE.read_text()) if ROUTE_PROGRESS_FILE.exists() else {}
    progress[route.name] = index
    ROUTE_PROGRESS_FILE.write_text(json.dumps(progress))
    wp = route.waypoints[index]
    print(f"{route.name}: going on from {index} ({wp.action} {wp.x},{wp.y},{wp.z} {wp.label})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--at", type=int, help="the waypoint to go on from")
    parser.add_argument("--watch", type=float, default=10.0, help="seconds of log after the start")
    parser.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for the start")
    args = parser.parse_args()
    stop_bot()
    if args.at is not None:
        set_start(args.at)
    subprocess.run(["./start-moonlight-gui.sh", "--start", "--minimized"], check=True)
    if not wait_for(STARTED, args.timeout):
        sys.exit(f"the cavebot did not start within {args.timeout:.0f} s")
    wait_for(re.compile(r"(?!)"), args.watch)  # matches nothing: just print --watch seconds


if __name__ == "__main__":
    main()
