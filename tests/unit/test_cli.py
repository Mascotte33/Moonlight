"""T40: CLI dispatch + the headless integration smoke (build_loop seam)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from moonlight import cli
from moonlight.calibrate import REGIONS, CalibrationProfile, Region


def make_profile() -> CalibrationProfile:
    p = CalibrationProfile(landmark=(100, 100))
    for i, name in enumerate(REGIONS):
        p.set_region(name, Region(10 + 40 * i, 20, 30, 10))
    return p


def write_profile(d: Path, name: str = "default") -> None:
    d.mkdir(parents=True, exist_ok=True)
    make_profile().save(d / f"{name}.json")


# -- dispatch -------------------------------------------------------------------


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "profiles" in capsys.readouterr().out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("moonlight ")


def test_no_subcommand_is_error() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code != 0


def test_profiles_lists(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_profile(tmp_path, "alpha")
    write_profile(tmp_path, "beta")
    assert cli.main(["--profiles-dir", str(tmp_path), "profiles"]) == 0
    assert capsys.readouterr().out.split() == ["alpha", "beta"]


def test_profiles_empty_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--profiles-dir", str(tmp_path / "none"), "profiles"]) == 0
    assert "No profiles" in capsys.readouterr().out


def test_calibrate_delegates() -> None:
    with mock.patch("moonlight.calibrate.main", return_value=0) as cal:
        assert cli.main(["calibrate", "--list"]) == 0
    cal.assert_called_once_with(["--list"])


def test_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_profile(tmp_path, "moonlight_user")
    with mock.patch("moonlight.input.find_arduino_port", return_value="COM9"):
        assert cli.main(["--profiles-dir", str(tmp_path), "check"]) == 0
    with mock.patch("moonlight.input.find_arduino_port", return_value=None):
        assert cli.main(["--profiles-dir", str(tmp_path), "check"]) == 1
    out = capsys.readouterr().out
    assert "no Arduino found" in out
    assert "log OCR: tesseract" in out  # F-21 health line, OK or WARN



def test_run_unknown_profile_fails_closed(tmp_path: Path) -> None:
    assert cli.main(["--profiles-dir", str(tmp_path), "run", "ghost"]) == 2


def test_missing_explicit_config_fails_closed(tmp_path: Path) -> None:
    assert cli.main(["--config", str(tmp_path / "x.json"), "profiles"]) == 0  # not needed
    assert cli.main(["--config", str(tmp_path / "x.json"), "run"]) == 2


def test_run_dispatch(tmp_path: Path) -> None:
    write_profile(tmp_path, "moonlight_user")
    loop = mock.Mock()
    with (
        mock.patch("moonlight.capture.ScreenCapture"),
        mock.patch("moonlight.input.ArduinoInput") as arduino,
        mock.patch.object(cli, "build_loop", return_value=loop) as build,
    ):
        assert cli.main(["--profiles-dir", str(tmp_path), "run"]) == 0
    build.assert_called_once()
    loop.run.assert_called_once()
    arduino.return_value.close.assert_called_once()  # releases + stops heartbeat


def test_run_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """F-03: the new --minimap-start arg is exposed and `run --help` exits 0."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["run", "--help"])
    assert exc.value.code == 0
    assert "minimap-start" in capsys.readouterr().out



def test_calibration_error_is_one_line_not_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F-27: a CalibrationError (e.g. cavebot without game_grid) exits 2 cleanly."""
    from moonlight.calibrate import CalibrationError

    def boom(_args: object) -> int:
        raise CalibrationError("region 'game_grid' is not calibrated")

    monkeypatch.setattr(cli, "_cmd_profiles", boom)
    assert cli.main(["profiles"]) == 2
    assert capsys.readouterr().out == "error: region 'game_grid' is not calibrated\n"


