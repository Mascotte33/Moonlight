"""Shared fixtures for the whole test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _empty_data_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point this PC's ``data_dir()`` at an empty temp folder.

    Tests must neither read the owner's calibration, seen monsters and item
    pictures nor write new ones there.
    """
    data = tmp_path_factory.mktemp("data")
    monkeypatch.setattr("moonlight.calibration.calibrate.data_dir", lambda: data)
    monkeypatch.setattr(
        "moonlight.looting.looting.LOOT_SHOTS_DIR", tmp_path_factory.mktemp("loot_rings")
    )


@pytest.fixture(autouse=True)
def _no_random_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exact waits: timing tests step the clock by the base values."""
    monkeypatch.setattr("moonlight.core.random_delay.RANDOM_DELAY_SPREAD", 0.0)
    monkeypatch.setattr("moonlight.core.random_delay.REACTION_SPREAD", 0.0)
    monkeypatch.setattr("moonlight.core.random_delay.TYPING_GAP_SECONDS", (0.0, 0.0))
    monkeypatch.setattr("moonlight.core.random_delay.HEAL_REACTION_SECONDS", (0.0, 0.0))


@pytest.fixture(autouse=True)
def _echo_raid_duds_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read or write the real list of tiles whose echo raid never triggered."""
    import moonlight.cavebot.cavebot as cavebot
    import moonlight.cavebot.echo_raids as echo_raids

    monkeypatch.setattr(echo_raids, "ECHO_RAID_DUDS_FILE", tmp_path / "echo_raid_duds.txt")
    monkeypatch.setattr(cavebot, "ROUTE_PROGRESS_FILE", tmp_path / "route_progress.json")
    monkeypatch.setattr(
        "moonlight.scripts.rotation.PLAYING_FILE", tmp_path / "rotation_playing.json"
    )
    monkeypatch.setattr(
        "moonlight.cavebot.floor_changes.FLOOR_CHANGES_FILE", tmp_path / "floor_changes.csv"
    )
