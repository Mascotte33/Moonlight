"""cavebot_rotate: the profile that fits the level runs (docs/prs/profiles.md#level-pick)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from moonlight.core.config import BotConfig
from moonlight.scripts import cavebot_rotate
from moonlight.scripts.cavebot_rotate import fitting_profiles, pick_profile
from moonlight.scripts.scripts import ScriptRunner


@pytest.fixture()
def profiles(tmp_path: Path) -> Path:
    for name, low, high in (
        ("Trolls", 20, 40),
        ("Amazons", 20, 40),
        ("Cults", 60, 100),
        ("Training", 0, 0),
    ):
        replace(BotConfig.default(), min_level=low, max_level=high).save(tmp_path / f"{name}.json")
    return tmp_path


def test_only_ranged_profiles_that_hold_the_level_fit(profiles: Path) -> None:
    assert fitting_profiles(30, profiles) == ["Amazons", "Trolls"]
    assert fitting_profiles(40, profiles) == ["Amazons", "Trolls"]  # both ends included
    assert fitting_profiles(50, profiles) == []  # Training has no range: never picked


def test_the_running_profile_stays_while_it_fits(profiles: Path) -> None:
    assert pick_profile(30, "Trolls", profiles, choose=lambda n: n[0]) == "Trolls"


def test_a_profile_that_no_longer_fits_is_replaced(profiles: Path) -> None:
    assert pick_profile(70, "Trolls", profiles) == "Cults"
    assert pick_profile(30, "Training", profiles, choose=lambda n: n[-1]) == "Trolls"


def test_no_fitting_profile_keeps_the_running_one(profiles: Path) -> None:
    assert pick_profile(50, "Trolls", profiles) is None


def _runner(profiles: Path, level: int | None, current: str) -> tuple[ScriptRunner, list[str]]:
    runner = ScriptRunner()
    switched: list[str] = []
    runner.profiles_dir = profiles
    runner.level = lambda: level
    runner.profile_name = lambda: current
    runner.switch_profile = switched.append
    return runner, switched


def test_the_script_switches_the_whole_profile(profiles: Path) -> None:
    runner, switched = _runner(profiles, 70, "Trolls")
    list(cavebot_rotate.run(runner))
    assert switched == ["Cults"]


@pytest.mark.parametrize(("level", "current"), [(30, "Trolls"), (50, "Trolls"), (None, "Trolls")])
def test_the_script_keeps_the_profile(profiles: Path, level: int | None, current: str) -> None:
    runner, switched = _runner(profiles, level, current)
    list(cavebot_rotate.run(runner))
    assert switched == []
