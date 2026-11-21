"""Turn a hunting video into route lines: the minimap in each frame gives the position.

``TrailFinder`` finds the radar's spot in each sampled frame with the bot's
own ``WorldMap`` match (near the last spot on its floor, then the floors
above and below, then every floor). ``clean_trail`` drops matches out of
walking reach of both neighbours. ``trail_lines`` writes the trail in
``tools/route.py new``'s line format: a walk every N sqm walked, the floor
change sqm when the floor changes, and ``#`` notes with the video time for
stops, floor changes and jumps (boat, teleport), naming the town spot a stop
was next to.

# doc: docs/prs/video-route.md#trail
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from moonlight.cavebot.route_plan import FloorMap
from moonlight.perception.worldmap import WorldMap

__all__ = ["Sample", "TrailFinder", "clean_trail", "trail_lines"]

Spot = tuple[int, int, int]
#: How fast a match may move between samples (sqm per second, hasted high
#: levels included), plus slack for a short gap.
MAX_SQM_PER_SECOND = 8.0
SLACK_SQM = 3
#: How far from the last spot on the old floor the floor change may be.
FIND_CHANGE_SQM = 3
#: A stop this close to a town spot from the saved routes names it.
PLACE_SQM = 4
FLOORS = range(16)
#: Every map colour has each channel a multiple of this.
PALETTE_STEP = 51


@dataclass(frozen=True)
class Sample:
    """Where the character was *seconds* into the video."""

    seconds: float
    spot: Spot


def _sqm(a: Spot, b: Spot) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _walkable_gap(a: Sample, b: Sample) -> bool:
    """True when walking (or one floor change) takes *a* to *b* in the time between."""
    allowed = MAX_SQM_PER_SECOND * abs(b.seconds - a.seconds) + SLACK_SQM
    return abs(a.spot[2] - b.spot[2]) <= 1 and _sqm(a.spot, b.spot) <= allowed


class TrailFinder:
    """The character's spot in one radar crop after another."""

    def __init__(self, world: WorldMap) -> None:
        self.world = world
        self.last: Spot | None = None

    def find(self, video_radar_rgb: np.ndarray) -> Spot | None:
        # Video compression moves every pixel off the map's 216-colour palette,
        # and the matcher masks off-palette pixels as map marks.
        radar_rgb = (np.round(video_radar_rgb / PALETTE_STEP) * PALETTE_STEP).astype(np.uint8)
        if self.last is not None:
            x, y, z = self.last
            for floor in (z, z - 1, z + 1):
                found = self.world.match(radar_rgb, floor, near=(x, y))
                if found is not None:
                    self.last = (*found, floor)
                    return self.last
        best: tuple[float, Spot] | None = None
        for floor in FLOORS:  # lost: the whole map, the best score wins
            found = self.world.match(radar_rgb, floor)
            if found is not None and (best is None or self.world.score < best[0]):
                best = (self.world.score, (*found, floor))
        if best is None:
            return None
        self.last = best[1]
        return self.last


def clean_trail(samples: list[Sample]) -> list[Sample]:
    """*samples* without a match that is out of walking reach of both neighbours."""
    kept: list[Sample] = []
    for i, sample in enumerate(samples):
        if kept and i + 1 < len(samples):
            before, after = kept[-1], samples[i + 1]
            if (
                _walkable_gap(before, after)
                and not _walkable_gap(before, sample)
                and not _walkable_gap(sample, after)
            ):
                continue  # a bad match between two that agree
        kept.append(sample)
    return kept


def _stays(samples: list[Sample]) -> Iterator[tuple[Spot, float, float]]:
    """(spot, first seconds, last seconds) of each run of samples on one spot."""
    i = 0
    while i < len(samples):
        j = i
        while j + 1 < len(samples) and samples[j + 1].spot == samples[i].spot:
            j += 1
        yield samples[i].spot, samples[i].seconds, samples[j].seconds
        i = j + 1


def _clock(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def _xyz(spot: Spot) -> str:
    return f"{spot[0]} {spot[1]} {spot[2]}"


def _place(spot: Spot, places: list[tuple[Spot, str]]) -> str:
    for at, name in places:
        if at[2] == spot[2] and _sqm(at, spot) <= PLACE_SQM:
            return f" next to {name}"
    return ""


def trail_lines(
    samples: list[Sample],
    floors: FloorMap,
    places: list[tuple[Spot, str]],
    *,
    every_sqm: int = 8,
    stop_seconds: float = 5.0,
) -> list[str]:
    """Route lines (``x y z [kind]``, ``# notes``) for a cleaned trail."""
    lines: list[str] = []
    walked = 0
    previous: tuple[Spot, float, float] | None = None
    for spot, first, last in _stays(samples):
        at = _clock(first)
        out: list[str] = []
        if previous is None:
            out.append(f"{_xyz(spot)}  # {at} start")
        elif previous[0][2] != spot[2]:
            old = previous[0]
            changes = [c for c in floors.changes_near(*old, FIND_CHANGE_SQM) if c.to[2] == spot[2]]
            if changes:
                change = changes[0]
                guess = "" if change.known else " (guess)"
                note = f"# {at} floor {old[2]} -> {spot[2]}{guess}"
                out.append(f"{_xyz(change.at)} {change.kind}  {note}")
            else:
                out.append(f"# {at} floor {old[2]} -> {spot[2]} near {_xyz(old)}: add it by hand")
            out.append(_xyz(spot))
        elif not _walkable_gap(Sample(previous[2], previous[0]), Sample(first, spot)):
            out.append(f"# {at} jumped from {_xyz(previous[0])} (boat, teleport?)")
            out.append(_xyz(spot))
        else:
            walked += _sqm(previous[0], spot)
            if walked >= every_sqm:
                out.append(_xyz(spot))
        if last - first >= stop_seconds:
            if not out or out[-1] != _xyz(spot):
                out.append(_xyz(spot))
            out.append(f"# {at} stood {last - first:.0f} s{_place(spot, places)}")
        if out:
            lines.extend(out)
            walked = 0
        previous = (spot, first, last)
    if previous is not None and walked:
        lines.append(f"{_xyz(previous[0])}  # {_clock(previous[2])} end")
    return lines
