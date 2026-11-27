"""cavebot_rotate: the profile that fits the level runs (docs/prs/profiles.md#level-pick)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from moonlight.core.config import BotConfig, LiveConfig
from moonlight.gui.settings_tab import PROFILE_OPTIONS
from moonlight.scripts import cavebot_rotate
from moonlight.scripts.cavebot_rotate import decide, fitting_profiles, pick_profile
from moonlight.scripts.scripts import ScriptRunner


@pytest.fixture()
def profiles(tmp_path: Path) -> Path:
    for name, low, high, rotate in (
        ("Trolls", 20, 40, True),
        ("Amazons", 20, 40, True),
        ("Cults", 60, 100, True),
        ("Training", 0, 0, True),
        ("Orcs", 20, 40, False),
    ):
        cfg = replace(BotConfig.default(), min_level=low, max_level=high, rotate=rotate)
        cfg.save(tmp_path / f"{name}.json")
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


def test_an_unticked_profile_is_never_picked(profiles: Path) -> None:
    assert "Orcs" not in fitting_profiles(30, profiles)
    assert pick_profile(30, "Orcs", profiles, choose=lambda n: n[0]) == "Amazons"


def test_an_unticked_running_profile_stays_without_another(profiles: Path) -> None:
    assert decide(70, "Orcs", profiles) == (
        "Cults",
        "Cavebot Rotate unticked",
    )
    name, why = decide(50, "Orcs", profiles)
    assert name is None and "unticked" in why and "no other ticked profile" in why


def test_profiles_start_unticked_and_keep_their_tick(tmp_path: Path) -> None:
    old = BotConfig.default().to_dict()
    del old["rotate"]  # a profile saved before the tick existed
    assert BotConfig.from_dict(old).rotate is False
    replace(BotConfig.default(), rotate=True).save(tmp_path / "a.json")
    assert BotConfig.load(tmp_path / "a.json").rotate is True


def test_the_tick_sits_under_max_level() -> None:
    fields = [o.field for o in PROFILE_OPTIONS]
    assert fields[fields.index("max_level") + 1] == "rotate"
    assert PROFILE_OPTIONS[fields.index("rotate")].label == "Cavebot Rotate"
    assert PROFILE_OPTIONS[fields.index("rotate")].control == "checkbox"


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


def test_switched_off_the_waypoint_does_nothing(profiles: Path) -> None:
    runner, switched = _runner(profiles, 70, "Trolls")
    runner.settings = LiveConfig(replace(BotConfig(), cavebot_rotate=False))
    assert runner.start("cavebot_rotate") is False
    assert switched == []


def test_the_switch_is_a_setting_of_this_pc() -> None:
    from moonlight.core.config import MACHINE_KEYS

    assert "cavebot_rotate" in MACHINE_KEYS and BotConfig.default().cavebot_rotate


@pytest.mark.parametrize(
    ("level", "current", "logged"),
    [
        (30, "Trolls", "Trolls kept: ticked and fits"),
        (70, "Trolls", "Trolls -> Cults: level 70 outside 20..40"),
        (30, "Orcs", "Orcs -> Amazons: Cavebot Rotate unticked"),
        (50, "Trolls", "Trolls kept: level 50 outside 20..40, but no other ticked profile fits"),
        (None, "Trolls", "Trolls kept, level not read"),
    ],
)
def test_every_run_logs_the_result_and_why(
    profiles: Path, caplog: pytest.LogCaptureFixture, level: int | None, current: str, logged: str
) -> None:
    runner, _ = _runner(profiles, level, current)
    with caplog.at_level("INFO"):
        list(cavebot_rotate.run(runner, choose=lambda n: n[0]))
    assert logged in caplog.text
