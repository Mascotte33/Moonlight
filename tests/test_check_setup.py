"""tools/check_setup.py: a profile missing a module's region reports FAIL."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import mock

import pytest

from moonlight.calibration.calibrate import REGIONS, CalibrationProfile, Region
from moonlight.core import wiring
from moonlight.core.config import BotConfig

_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "check_setup.py"
_spec = importlib.util.spec_from_file_location("check_setup", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check_setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_setup)


def test_missing_region_reports_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    p = CalibrationProfile(landmark=(100, 100))
    for i, name in enumerate(REGIONS):
        p.set_region(name, Region(10 + 40 * i, 20, 30, 10))
    p.save(tmp_path / "moonlight_user.json")
    config = BotConfig.default()
    with (
        # A region outside the standard calibration set.
        mock.patch.dict(wiring.MODULE_REGIONS, {"healing": ("hp_bar", "capacity")}),
        mock.patch.object(check_setup, "_load_config", return_value=config),
        mock.patch("moonlight.hardware.input.find_arduino_port", return_value="COM9"),
    ):
        assert check_setup.main(["--profiles-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] module healing: missing calibration region(s) capacity" in out
