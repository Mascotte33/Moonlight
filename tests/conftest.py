"""Shared fixtures for the whole test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _empty_profiles_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the default profiles folder at an empty temp folder.

    Tests must neither read the owner's calibrations and seen monsters in
    ``moonlight/profiles/`` nor write new ones there.
    """
    profiles = tmp_path_factory.mktemp("profiles")
    monkeypatch.setattr("moonlight.calibration.calibrate.default_profiles_dir", lambda: profiles)
    monkeypatch.setattr("moonlight.calibration.profile.default_profiles_dir", lambda: profiles)
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
