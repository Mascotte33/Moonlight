"""The ``boat`` waypoint: find the captain by name, walk next to him, talk, land.

# doc: docs/prs/boat.md#boat-waypoint
"""

from __future__ import annotations

import shutil
from concurrent.futures import Future
from pathlib import Path

import cv2
import numpy as np
import pytest

import moonlight.cavebot.cavebot as cb
from moonlight.cavebot import travel
from moonlight.cavebot.cavebot import RouteError, RouteFileLoader, Waypoint
from moonlight.cavebot.recorder import waypoint_json
from moonlight.cavebot.route_map import legs
from moonlight.cavebot.travel import TravelNpc
from moonlight.core.state import Position
from moonlight.perception.npc_names import find_named, name_likeness
from tests.test_cavebot import Sim, SteppedClicker, run_until, walk

BLUEBEAR = TravelNpc("ship", "Thais", "Captain Bluebear", 100, 100, 7)
FEARLESS = TravelNpc("ship", "Venore", "Captain Fearless", 500, 500, 7)
NPCS = (BLUEBEAR, FEARLESS)


def _boat_sim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, start: Position
) -> tuple[Sim, dict[str, tuple[int, int, int] | None], SteppedClicker]:
    """A sim whose name reader sees the captain on sqm ``seen["captain"]`` (None: not on screen)."""
    clicker = SteppedClicker()
    monkeypatch.setattr(cb, "find_chat", lambda frame: (len(
        [c for c in clicker.clicks if c[0] == (500, 600)]) % 2 == 0, (500, 600)))  # fmt: skip
    monkeypatch.setattr(cb, "captain_at", lambda x, y, z: travel.captain_at(x, y, z, NPCS))
    monkeypatch.setattr(cb, "landed", lambda d, x, y, z: travel.landed(d, x, y, z, NPCS))
    monkeypatch.setattr(cb, "BOAT_SHOTS_DIR", tmp_path / "boat")
    frame = np.zeros((10, 10, 3), np.uint8)
    sim = Sim([Waypoint(100, 100, 7, "boat", "venore"), walk(510, 500)], start,
              frames=lambda: frame, clicker=clicker)  # fmt: skip
    seen: dict[str, tuple[int, int, int] | None] = {"captain": None}

    def read(frame: np.ndarray, grid: object, name: str) -> Future[object]:
        assert name == "Captain Bluebear"
        done: Future[object] = Future()
        tile, pos = seen["captain"], sim.pos
        found = None
        if tile is not None and pos is not None:
            found = ((tile[0] - pos.x + 7, tile[1] - pos.y + 5), "CaptainBluebear", 0.97)
        done.set_result(found)
        return done

    sim.bot._read_names = read  # type: ignore[assignment]
    sim.bot.start()
    return sim, seen, clicker


def test_boat_walks_next_to_the_captain_talks_and_lands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sim, seen, clicker = _boat_sim(monkeypatch, tmp_path, Position(95, 100, 7))
    seen["captain"] = (98, 101, 7)  # wandered off his home sqm
    run_until(sim, 1.0)
    assert sim.bot._boat is not None and sim.bot._boat.captain_tile == (98, 101, 7)
    assert clicker.clicks  # walking next to him
    sim.pos = Position(97, 100, 7)
    run_until(sim, 12)
    keys = [line[2:] for line in sim.fake.sent_lines if line.startswith("K ")]
    assert keys[-15:] == [
        "shift+F11",
        "h",
        "i",
        "enter",
        *"venore",
        "enter",
        "y",
        "e",
        "s",
        "enter",
    ]
    assert sim.route.current == 0 and sim.bot.alarm is None  # sailing
    sim.pos = Position(503, 498, 7)  # landed next to Captain Fearless
    sim.step()
    assert sim.route.current == 1 and sim.bot.alarm is None


def test_boat_follows_the_captain_when_he_wanders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sim, seen, _ = _boat_sim(monkeypatch, tmp_path, Position(95, 100, 7))
    seen["captain"] = (98, 101, 7)
    run_until(sim, 1.0)
    seen["captain"] = (101, 103, 7)
    run_until(sim, 2.0)
    assert sim.bot._boat is not None and sim.bot._boat.captain_tile == (101, 103, 7)
    assert sim.bot._talk is None  # not next to him yet


def test_boat_alarms_when_the_captain_is_not_on_screen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sim, _, _ = _boat_sim(monkeypatch, tmp_path, Position(99, 100, 7))
    run_until(sim, 14.5)
    assert sim.bot.is_active() and sim.bot.alarm is None
    run_until(sim, 16)
    assert sim.bot.alarm == "boat" and not sim.bot.is_active()
    assert list((tmp_path / "boat").glob("boat_*.png"))  # what the bot saw


def test_boat_walks_on_to_the_captains_sqm_before_it_gives_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Thais 2026-11-26: his sqm at the view's edge, he out of the view: no alarm there.
    sim, _, clicker = _boat_sim(monkeypatch, tmp_path, Position(94, 100, 7))
    run_until(sim, 20)
    assert sim.bot.alarm is None and clicker.clicks  # still walking to his sqm
    sim.pos = Position(98, 100, 7)  # 2 sqm off it: looks from here
    run_until(sim, 34)
    assert sim.bot.alarm is None
    run_until(sim, 36)
    assert sim.bot.alarm == "boat"


def test_boat_alarms_when_the_trip_does_not_land(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sim, seen, _ = _boat_sim(monkeypatch, tmp_path, Position(99, 100, 7))
    seen["captain"] = (100, 100, 7)  # next to the player already
    run_until(sim, 12)
    assert sim.bot.is_active() and sim.bot._boat is not None
    assert sim.bot._boat.sail_until is not None  # talked
    run_until(sim, 30)
    assert sim.bot.alarm == "boat" and not sim.bot.is_active()


def test_boat_without_a_travel_npc_on_its_sqm_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cb, "captain_at", lambda x, y, z: None)
    frame = np.zeros((10, 10, 3), np.uint8)
    sim = Sim([Waypoint(100, 100, 7, "boat", "venore")], Position(99, 100, 7), frames=lambda: frame)
    sim.bot.start()
    sim.step()
    assert sim.bot.alarm == "boat"


def test_boat_far_from_the_harbour_walks_there_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sim, _, clicker = _boat_sim(monkeypatch, tmp_path, Position(60, 100, 7))
    run_until(sim, 30)
    assert sim.bot.alarm is None and clicker.clicks  # out of view: no search, no timeout
    assert sim.bot._boat is not None and sim.bot._boat.since is None


# -- route file, legs, travel data -------------------------------------------


def test_boat_route_keeps_its_destination_and_rejects_a_bad_one() -> None:
    raw = {
        "name": "r",
        "waypoints": [{"x": 1, "y": 2, "z": 7, "action": "boat", "label": "venore"}],
    }
    wp = RouteFileLoader.parse(raw).waypoints[0]
    assert wp == Waypoint(1, 2, 7, "boat", "venore")
    assert waypoint_json(wp) == raw["waypoints"][0]
    for label in ("", "venore!", "1"):
        bad = {
            "name": "r",
            "waypoints": [{"x": 1, "y": 2, "z": 7, "action": "boat", "label": label}],
        }
        with pytest.raises(RouteError):
            RouteFileLoader.parse(bad)


def test_no_leg_across_a_goto_or_after_a_boat() -> None:
    wps = [
        Waypoint(100, 100, 7),
        Waypoint(0, 0, 0, "goto", "Depot"),
        Waypoint(300, 300, 7),
        Waypoint(310, 300, 6, "boat", "venore"),
        Waypoint(900, 900, 6),
        Waypoint(905, 900, 6),
    ]
    assert legs(wps) == [(2, 3), (4, 5), (5, 0)]
    assert legs([*wps, Waypoint(0, 0, 0, "goto", "Depot")]) == [(2, 3), (4, 5)]


def test_travel_npcs_file_has_the_free_captains() -> None:
    npcs = travel.load_travel_npcs()
    assert travel.captain_at(32310, 32210, 6, npcs) == TravelNpc(
        "ship", "Thais", "Captain Bluebear", 32310, 32210, 6
    )
    assert travel.captain_at(32388, 31821, 6, npcs) is not None  # Carlin
    assert travel.captain_at(32732, 31669, 6, npcs) is not None  # Ab'Dendriel
    assert travel.landed("venore", 32950, 32025, 6, npcs)
    assert not travel.landed("venore", 32950, 32040, 6, npcs)  # 19 sqm off
    assert not travel.landed("venore", 32950, 32025, 7, npcs)  # another floor
    assert travel.landed("ab dendriel", 32732, 31670, 6, npcs)


# -- reading names --------------------------------------------------------------


def test_name_likeness_ignores_case_and_spaces() -> None:
    assert name_likeness("CaptainBluebear", "Captain Bluebear") == 1.0
    assert name_likeness("Captain Bluebcar", "Captain Bluebear") >= 0.8
    assert name_likeness("Captain Fearless", "Captain Bluebear") < 0.7  # not him
    assert name_likeness("", "Captain Bluebear") == 0.0


GRID = (0, 0, 15 * 96, 11 * 96)


def _named_frame(names: dict[tuple[int, int], str]) -> np.ndarray:
    frame = np.full((GRID[3], GRID[2], 3), 90, np.uint8)
    for (col, row), name in names.items():
        x, y = col * 96 + 33, row * 96 - 9  # the bar over that tile, like the client
        frame[y : y + 4, x : x + 31] = 0
        frame[y + 1 : y + 3, x + 1 : x + 30] = (0, 192, 0)
        org = (x + 15 - len(name) * 4, y - 4)
        cv2.putText(frame, name, org, cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(frame, name, org, cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 200, 0), 1, cv2.LINE_AA)
    return frame


needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="no Tesseract")


@needs_tesseract
def test_find_named_picks_the_captain_among_other_creatures() -> None:
    frame = _named_frame({(8, 5): "Rat", (10, 3): "Captain Bluebear", (3, 8): "Captain Fearless"})
    found = find_named(frame, GRID, "Captain Bluebear")
    assert found is not None and found[0] == (10, 3) and found[2] >= 0.8


@needs_tesseract
def test_find_named_finds_nobody_when_no_name_is_close() -> None:
    frame = _named_frame({(8, 5): "Rat", (3, 8): "Captain Fearless"})
    assert find_named(frame, GRID, "Captain Bluebear") is None
