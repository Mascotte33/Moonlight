"""Calibration profile manager (T40).

Thin, single source of truth for ``profiles/<name>.json`` on top of
:class:`moonlight.calibrate.CalibrationProfile`. The GUI and
``tools/check_setup.py`` delegate here.
"""

from __future__ import annotations

from pathlib import Path

from moonlight.calibrate import CalibrationProfile, default_profiles_dir

__all__ = ["ProfileManager"]


class ProfileManager:
    """Lists and loads calibration profiles from one directory."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory) if directory is not None else default_profiles_dir()

    def path(self, name: str) -> Path:
        """File path of profile *name*."""
        return self.directory / f"{name}.json"

    def list_profiles(self) -> list[str]:
        """Profile names (file stems), sorted; empty when the directory is missing."""
        if not self.directory.is_dir():
            return []
        return sorted(p.stem for p in self.directory.glob("*.json"))

    def load(self, name: str) -> CalibrationProfile:
        """Load and validate profile *name*.

        Raises:
            ValueError: If the profile does not exist.
            CalibrationError: If the file is malformed or incomplete.
        """
        available = self.list_profiles()
        if name not in available:
            listing = ", ".join(available) if available else "(none)"
            raise ValueError(f"Unknown profile {name!r}. Available: {listing}")
        profile = CalibrationProfile.load(self.path(name))
        profile.validate()
        return profile
