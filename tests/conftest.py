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
