"""tools/check_setup.py: a profile missing a module's region reports FAIL."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest

from moonlight.calibrate import REGIONS, CalibrationProfile, Region
from moonlight.config import BotConfig
from moonlight.decision.looting import LootingConfig

_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_setup.py"
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
    # Looting needs ``loot_window``, which is not in the standard region set.
    config = replace(BotConfig.default(), looting=LootingConfig())
    with (
        mock.patch.object(check_setup, "_load_config", return_value=config),
        mock.patch("moonlight.input.find_arduino_port", return_value="COM9"),
    ):
        assert check_setup.main(["--profiles-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] module looting: missing calibration region(s) loot_window" in out
