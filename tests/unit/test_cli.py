"""T40: CLI dispatch + the headless integration smoke (build_loop seam)."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

from moonlight import cli
from moonlight.calibrate import REGIONS, CalibrationProfile, Region
from moonlight.config import BotConfig, CavebotConfig
from moonlight.decision.buffs import BuffManagerConfig
from moonlight.decision.cavebot import Route, Waypoint
from moonlight.decision.looting import LootingConfig
from moonlight.decision.refill import RefillConfig
from moonlight.input import FakeInput
from moonlight.loop import LoopConfig
from moonlight.state import WorldState


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
    assert "no Arduino found" in capsys.readouterr().out


def test_check_module_regions_reports_missing() -> None:
    """F-11: check_module_regions names the missing region(s) per enabled module."""
    profile = make_profile()
    del profile.regions["game_grid"]  # break cavebot only
    cfg = replace(
        BotConfig.default(),
        cavebot=CavebotConfig(Route("hunt", waypoints=[Waypoint(100, 100, 7)])),
    )
    report = cli.check_module_regions(profile, cfg)
    assert report["cavebot"] == ["game_grid"]
    assert report["healing"] == []
    assert report["targeting"] == []
    assert report["alarms"] == []
    # Only enabled modules appear in the report.
    assert set(report) == {"healing", "targeting", "cavebot", "alarms"}


def test_check_module_regions_all_present() -> None:
    """F-11: a complete profile yields an empty (OK) report for every module."""
    profile = make_profile()
    cfg = replace(
        BotConfig.default(),
        cavebot=CavebotConfig(Route("hunt", waypoints=[Waypoint(100, 100, 7)])),
    )
    report = cli.check_module_regions(profile, cfg)
    assert report["cavebot"] == []
    assert all(missing == [] for missing in report.values())


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


# -- headless smoke ---------------------------------------------------------------


def test_headless_smoke_all_modules(tmp_path: Path) -> None:
    """Real build_loop wiring: FakeInput + synthetic frames, a few ticks."""
    (tmp_path / "refill.json").write_text(
        json.dumps({"name": "refill", "waypoints": [{"x": 1, "y": 1, "z": 7}]})
    )
    cfg = replace(
        BotConfig.default(),
        loop=LoopConfig(enable_f12_monitor=False),
        hotkeys={"exura gran": "F1", "exura": "F2"}
        | {b.spell: f"F{3 + i}" for i, b in enumerate(BuffManagerConfig().buffs)},
        buffs=BuffManagerConfig(),
        cavebot=CavebotConfig(Route("hunt", waypoints=[Waypoint(100, 100, 7)])),
        looting=LootingConfig(),
        refill=RefillConfig(routes_dir=tmp_path),
        templates_dir=tmp_path / "templates",
        # F-09 / F-10: enable the perception sources these modules require.
        monster_name_source=True,
        capacity_source=True,
        buff_icon_source=True,
    ).validate()
    rng = np.random.default_rng(0)
    frames = [rng.integers(0, 256, (600, 800, 3), dtype=np.uint8) for _ in range(3)]
    calls = iter(range(10**6))

    def frame_provider() -> np.ndarray:
        return frames[next(calls) % len(frames)]

    state = WorldState()
    inp = FakeInput()
    now = [0.0]

    def sleep(dt: float) -> None:
        now[0] += max(0.0, dt)

    loop = cli.build_loop(
        cfg, make_profile(), inp, frame_provider=frame_provider, state=state,
        clock=lambda: now[0], sleep_fn=sleep,
    )
    for _ in range(5):
        loop.tick()
        now[0] += 0.05
    assert next(calls) > 0  # perception pulled frames
    assert loop.modules.cavebot is not None and loop.modules.refill is not None
    snap = state.snapshot()
    assert snap.screen_ok and snap.last_update > 0  # perception wrote WorldState


# -- F-01: no auto-attack with unknown names (interim) -------------------------


def test_targeting_without_resolver_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """F-01: enabling targeting without a name resolver emits a visible warning.

    The default config has targeting on and no name resolver is wired (T34),
    so build_loop must emit an explicit, visible warning at start rather than
    silently bypassing the no-nameless-attack rule.
    """
    cfg = BotConfig.default()
    assert cfg.targeting is not None  # targeting is on by default
    state = WorldState()
    frame = np.zeros((600, 800, 3), dtype=np.uint8)

    def frame_provider() -> np.ndarray:
        return frame

    with caplog.at_level(logging.WARNING, logger="moonlight.cli"):
        cli.build_loop(
            cfg,
            make_profile(),
            FakeInput(),
            frame_provider=frame_provider,
            state=state,
        )
    messages = [r.getMessage() for r in caplog.records]
    assert any("name resolver" in m for m in messages), (
        f"F-01 no-resolver warning did not fire; got: {messages!r}"
    )


def test_run_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """F-03: the new --minimap-start arg is exposed and `run --help` exits 0."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["run", "--help"])
    assert exc.value.code == 0
    assert "minimap-start" in capsys.readouterr().out


# -- F-03: absolute minimap seed wiring ----------------------------------------


def test_minimap_start_seeds_minimap_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-03 wiring: minimap_start flows from config into MinimapReader.

    Verifies the config's absolute-start seed (F-03) reaches the perception
    layer: make_perceive passes minimap_start through to MinimapReader as
    initial_position, so reported positions are absolute world coordinates.
    """
    from moonlight.state import Position

    captured: dict[str, object] = {}

    class _FakeMinimap:
        def __init__(
            self,
            profile: CalibrationProfile,
            floor_reader: object | None = None,
            initial_position: object | None = None,
        ) -> None:
            captured["initial_position"] = initial_position

        def read_position(self, frame: np.ndarray) -> object:
            return None

    monkeypatch.setattr("moonlight.perception.minimap.MinimapReader", _FakeMinimap)
    state = WorldState()
    frame = np.zeros((600, 800, 3), dtype=np.uint8)

    def frame_provider() -> np.ndarray:
        return frame

    # With a seed: MinimapReader must receive it as initial_position.
    cli.make_perceive(
        state, make_profile(), frame_provider, minimap_start=(100, 200, 7)
    )
    assert captured["initial_position"] == Position(100, 200, 7)

    # Without a seed: initial_position is None (legacy relative behavior).
    captured.clear()
    cli.make_perceive(state, make_profile(), frame_provider, minimap_start=None)
    assert captured["initial_position"] is None
