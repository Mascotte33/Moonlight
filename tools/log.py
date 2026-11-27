#!/usr/bin/env python3
"""Read ``logs/bot.log`` without the noise, or wait for a line.

Usage:
    python tools/log.py                        # the last 30 lines that matter
    python tools/log.py -n 80 --since 23:40    # from 23:40 on (bot.log.1 too)
    python tools/log.py --grep "reached waypoint|stuck"
    python tools/log.py --wait "going on from|could not start" --timeout 60
    python tools/log.py --all                  # keep the noise

Noise is the 10 s tick timing, perception misses and the window lookups
(``NOISE``). Lines print as ``HH:MM:SS module message``. ``--grep`` and
``--wait`` take a Python regex. ``--wait`` follows the log from now and exits
0 on the first new matching line (printing the new lines up to it), 1 on
timeout.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Iterator
from pathlib import Path

LOG = Path("logs") / "bot.log"
NOISE = re.compile(
    r"tick timing:|perception failed:|Players window title not found|max mana:"
    r"|map: \d+ new tiles|perception\.stamina level"
)
_LINE = re.compile(r"^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d),\d+ (?:moonlight\.)?(\S+) (.*)$")


def short(line: str) -> str:
    """``2026-11-27 23:42:02,103 moonlight.cavebot.cavebot x`` -> ``23:42:02 cavebot.cavebot x``."""
    m = _LINE.match(line)
    return f"{m[1]} {m[2]} {m[3]}" if m else line


def wanted(line: str, keep_noise: bool, grep: re.Pattern[str] | None) -> bool:
    if not keep_noise and NOISE.search(line):
        return False
    return grep is None or bool(grep.search(line))


def read_lines(since: str | None, log: Path = LOG) -> list[str]:
    """The log's lines, from *since* (``HH:MM[:SS]``, today's log and the one before) on."""
    files = [log] if since is None else [log.with_suffix(".log.1"), log]
    lines: list[str] = []
    for path in files:
        if path.exists():
            lines += path.read_text(encoding="utf-8", errors="replace").splitlines()
    if since is not None:
        lines = [ln for ln in lines if (m := _LINE.match(ln)) is None or m[1] >= since]
    return lines


def follow(log: Path = LOG) -> Iterator[str]:
    """New lines of *log* from now on (a rotation starts over at the new file)."""
    handle = log.open(encoding="utf-8", errors="replace")
    handle.seek(0, 2)
    while True:
        line = handle.readline()
        if line:
            yield line.rstrip("\n")
            continue
        if log.exists() and log.stat().st_size < handle.tell():
            handle.close()
            handle = log.open(encoding="utf-8", errors="replace")
        yield ""  # nothing new: the caller checks its clock
        time.sleep(0.3)


def wait_for(
    pattern: re.Pattern[str], timeout: float, keep_noise: bool = False, log: Path = LOG
) -> bool:
    """Print new lines until one matches *pattern*; False after *timeout* seconds."""
    deadline = time.monotonic() + timeout
    for line in follow(log):
        if line and wanted(line, keep_noise, None):
            print(short(line), flush=True)
            if pattern.search(line):
                return True
        if time.monotonic() > deadline:
            return False
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-n", type=int, default=30, help="how many lines (default 30)")
    parser.add_argument("--since", help="HH:MM or HH:MM:SS")
    parser.add_argument("--grep", help="only lines matching this Python regex")
    parser.add_argument("--wait", help="follow the log until a new line matches this regex")
    parser.add_argument("--timeout", type=float, default=60.0, help="--wait: seconds (default 60)")
    parser.add_argument("--all", action="store_true", help="keep the noise lines")
    args = parser.parse_args()
    if args.wait:
        sys.exit(0 if wait_for(re.compile(args.wait), args.timeout, args.all) else 1)
    grep = re.compile(args.grep) if args.grep else None
    lines = [ln for ln in read_lines(args.since) if wanted(ln, args.all, grep)]
    for line in lines[-args.n :]:
        print(short(line))


if __name__ == "__main__":
    main()
