"""Offline tests for the extras: presses, player timer, alarms, pictures."""

# doc: docs/prs/extras.md#extras

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from moonlight.core.state import WorldState
from moonlight.extras.extras import ICONS, Extras, ExtrasConfig, find_icons, save_screenshot

#: Every extra on but the buff potion and spell buff (they press whenever their icon is missing).
ALL = ExtrasConfig(
    food=True,
    food_hotkey="F1",
    haste=True,
    haste_hotkey="F2",
    buff_hotkey="F3",
    spell_buff_hotkey="F6",
    ring=True,
    ring_hotkey="F4",
    amulet=True,
    amulet_hotkey="F5",
)
PICTURE = np.zeros((2, 2, 3), np.uint8)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def extras(
    config: ExtrasConfig, clock: Clock, *icons: str, pictures: tuple[str, ...] = ()
) -> Extras:
    """An Extras that sees *icons*; *pictures* are the loaded ones (default: all)."""
    names = pictures or tuple(ICONS)
    e = Extras(config, clock=clock, pictures={n: PICTURE for n in names})
    e.icons = frozenset(icons)
    return e


def test_food_ring_amulet_when_their_picture_shows() -> None:
    clock = Clock()
    assert extras(ALL, clock).due(walking=False) is None
    assert extras(ALL, clock, "hungry").due(walking=False) == "F1"
    assert extras(ALL, clock, "ring_empty").due(walking=False) == "F4"
    assert extras(ALL, clock, "amulet_empty").due(walking=False) == "F5"


def test_one_key_waits_between_presses() -> None:
    clock = Clock()
    e = extras(ALL, clock, "hungry")
    e.pressed("F1")
    clock.now = 1.0
    assert e.due(walking=False) is None
    clock.now = 2.0
    assert e.due(walking=False) == "F1"


def test_haste_only_walking_without_haste_or_pz() -> None:
    clock = Clock()
    assert extras(ALL, clock).due(walking=True) == "F2"
    assert extras(ALL, clock).due(walking=False) is None
    assert extras(ALL, clock, "haste").due(walking=True) is None
    assert extras(ALL, clock, "pz").due(walking=True) is None
    # No haste picture: the bot cannot tell, so it never presses.
    assert extras(ALL, clock, pictures=("pz",)).due(walking=True) is None


def test_buff_when_its_icon_is_missing() -> None:
    clock = Clock()
    buff = replace(ALL, buff=True)
    assert extras(buff, clock).due(walking=False) == "F3"
    assert extras(buff, clock, "buff").due(walking=False) is None


def test_spell_buff_when_its_icon_is_missing() -> None:
    clock = Clock()
    spell = replace(ALL, spell_buff=True)
    assert extras(spell, clock).due(walking=False) == "F6"
    assert extras(spell, clock, "spell_buff").due(walking=False) is None
    assert extras(spell, clock, pictures=("hungry",)).due(walking=False) is None  # no picture


def test_player_timer_alarm_refill_and_reset() -> None:
    clock = Clock()
    config = ExtrasConfig(player_minutes=1.0, player_refill=True, player_alarm=True)
    e = extras(config, clock)
    here = WorldState(players=("abc",))
    e.observe(here)
    clock.now = 59.0
    e.observe(here)
    assert e.alarm() is None and not e.refill_due
    clock.now = 60.0
    e.observe(here)
    assert e.alarm() == "player in sight over 1 min" and e.refill_due
    clock.now = 61.0
    e.observe(here)
    assert not e.refill_due  # once, at the limit
    clock.now = 120.0  # gone 59 s: the timer stays
    e.observe(WorldState())
    assert e.staying
    clock.now = 121.0  # gone 60 s: reset
    e.observe(WorldState())
    assert not e.staying and e.alarm() is None


def test_pm_alarm() -> None:
    clock = Clock()
    assert extras(ExtrasConfig(pm_alarm=True), clock, "pm").alarm() == "private message"
    assert extras(ExtrasConfig(), clock, "pm").alarm() is None


def test_an_extra_on_needs_a_hotkey() -> None:
    with pytest.raises(ValueError):
        ExtrasConfig(food=True).validate()
    with pytest.raises(ValueError):
        ExtrasConfig(player_minutes=0).validate()


def test_find_icons_and_scan_once_a_second() -> None:
    rng = np.random.default_rng(1)
    frame = rng.integers(0, 255, (60, 80, 3), dtype=np.uint8)
    pictures = {
        "hungry": frame[10:20, 30:45].copy(),
        "pm": rng.integers(0, 255, (10, 10, 3), dtype=np.uint8),
    }
    assert find_icons(frame, pictures) == {"hungry"}
    clock, grabs = Clock(), []

    def grab() -> np.ndarray:
        grabs.append(1)
        return frame

    e = Extras(ALL, grab=grab, clock=clock, pictures=pictures)
    e.observe(WorldState())
    clock.now = 0.5
    e.observe(WorldState())
    assert e.icons == {"hungry"} and len(grabs) == 1


def test_screenshots_keep_the_newest(tmp_path: Path) -> None:
    for i in range(3):
        (tmp_path / f"2026010{i}-000000.png").write_bytes(b"")
    save_screenshot(np.zeros((4, 4, 3), np.uint8), tmp_path, keep=2)
    names = sorted(p.name for p in tmp_path.glob("*.png"))
    assert len(names) == 2 and names[0] == "20260102-000000.png"
