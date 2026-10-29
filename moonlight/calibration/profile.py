"""Calibration profile manager (T40).

Thin, single source of truth for ``profiles/<name>.json`` on top of
:class:`moonlight.calibration.calibrate.CalibrationProfile`. The GUI and
``tools/check_setup.py`` delegate here.
"""

from __future__ import annotations

import re
from pathlib import Path

from moonlight.calibration.calibrate import CalibrationProfile, default_profiles_dir

__all__ = ["ProfileManager"]

_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")


class ProfileManager:
    """Lists and loads calibration profiles from one directory."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory) if directory is not None else default_profiles_dir()

    @staticmethod
    def valid_name(name: str) -> bool:
        """True for 1 to 40 letters, digits, ``_`` or ``-`` (no paths, no dots)."""
        return _NAME.fullmatch(name) is not None

    def path(self, name: str) -> Path:
        """File path of profile *name*."""
        return self.directory / f"{name}.json"

    def shot_path(self, name: str) -> Path:
        """Screenshot profile *name* was calibrated on, for the tab's summary."""
        return self.directory / f"{name}.png"

    def delete(self, name: str) -> None:
        """Remove profile *name* and its screenshot; missing files are fine."""
        self.path(name).unlink(missing_ok=True)
        self.shot_path(name).unlink(missing_ok=True)

    def list_profiles(self) -> list[str]:
        """Profile names (file stems), sorted; empty when the directory is missing.

        Other files there, such as ``<profile>.seen_monsters.json``, have no
        valid profile name and are left out.
        """
        if not self.directory.is_dir():
            return []
        return sorted(p.stem for p in self.directory.glob("*.json") if self.valid_name(p.stem))

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
