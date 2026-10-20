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
    out = capsys.readouterr().out
    assert "no Arduino found" in out
    assert "log OCR: tesseract" in out  # F-21 health line, OK or WARN


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
    assert next(calls) == 5  # P1: exactly one capture per tick, shared by all
    assert loop.modules.cavebot is not None and loop.modules.refill is not None
    # F-20: every module that sends shares the loop's wire limiter.
    m = loop.modules
    assert m.cavebot is not None and m.looting is not None and m.refill is not None
    for backend in (m.cavebot._input, m.cavebot._clicker._input, m.looting._input,
                    m.refill._input):
        assert backend is loop.input
    snap = state.snapshot()
    assert snap.screen_ok and snap.last_update > 0  # perception wrote WorldState


def test_frame_cache_captures_once_per_refresh() -> None:
    calls: list[int] = []

    def provider() -> np.ndarray:
        calls.append(1)
        return np.full((2, 2, 3), len(calls), dtype=np.uint8)

    cache = cli.FrameCache(provider)
    first = cache()  # nothing cached yet: captures
    assert cache() is first and len(calls) == 1
    fresh = cache.refresh()
    assert cache() is fresh and len(calls) == 2


def test_capture_regions_lists_tick_regions_or_falls_back_to_full() -> None:
    profile = make_profile()
    cfg = BotConfig.default()
    names = ("battle_list", "floor_indicator", "hp_bar", "minimap", "mp_bar")
    assert cli.capture_regions(profile, cfg) == [
        profile.get_region(n).to_tuple for n in names
    ]
    del profile.regions["minimap"]
    assert cli.capture_regions(profile, cfg) is None  # uncalibrated: full grab


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


def test_lost_minimap_clears_state_position(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-17: a lost tracker publishes "unknown" (0,0,0), not a stale position."""
    from moonlight.state import Position

    class _LostMinimap:
        lost = True

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def read_position(self, frame: np.ndarray) -> object:
            return None

    monkeypatch.setattr("moonlight.perception.minimap.MinimapReader", _LostMinimap)
    state = WorldState()
    state.position = Position(100, 200, 7)
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    cli.make_perceive(state, make_profile(), lambda: frame)()
    assert state.screen_ok
    assert state.position == Position(0, 0, 0)


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


def test_tick_grabber_folds_the_cursor_read_into_one_grab_per_tick() -> None:
    """P2 + P5: a clicking tick still makes ONE screen grab (regions + cursor ROI)."""
    from moonlight.clicker import ClickController
    from moonlight.perception.cursor import CursorReader

    fake = FakeInput(cursor=(80, 80))
    tpl = np.zeros((11, 11), dtype=np.uint8)
    tpl[:, 5] = tpl[5, :] = 255
    tpl[1, 1], tpl[9, 9] = 250, 200
    grabs: list[list[tuple[int, int, int, int]] | None] = []

    class Capture:
        def get_frame(self, regions: list[tuple[int, int, int, int]] | None) -> np.ndarray:
            grabs.append(regions)
            frame = np.full((200, 320, 3), 40, dtype=np.uint8)
            x, y = fake.cursor_position
            frame[y - 5 : y + 6, x - 5 : x + 6] = tpl[:, :, None]
            return frame

    now = [0.0]
    sleeps: list[float] = []
    tick_regions = [(0, 0, 10, 10)]
    grabber = cli.TickGrabber(
        Capture(),  # type: ignore[arg-type]
        tick_regions, 20.0, clock=lambda: now[0], sleep_fn=sleeps.append,
    )
    reader = CursorReader(cursor_region=(0, 0, 320, 200), cursor_templates=[tpl])
    clicker = ClickController(
        fake, reader, lambda: pytest.fail("full frame used"), roi_provider=grabber.roi,
        clock=lambda: now[0], sleep_fn=lambda s: None,
    )
    grabber.attach(clicker)
    clicker.request((150, 90))
    result = None
    for _ in range(10):
        n = len(grabs)
        grabber.frame()
        if result is None:
            result = clicker.step()
        assert len(grabs) == n + 1  # never a second grab in a tick
        now[0] += 0.05
    assert result is True and fake.sent_lines[-1] == "C L"
    assert grabs[0] == [*tick_regions, (0, 0, 320, 200)]  # first read: full region
    assert grabs[1] == [*tick_regions, (150 - 48, 90 - 48, 96, 96)]  # then the ROI
    assert grabs[-1] == tick_regions  # idle: no cursor read
    assert sleeps == []  # a tick gap (50 ms) already covers the 20 ms settle
    # A grab sooner than settle after the move waits out the remainder.
    clicker.request((10, 10))
    grabber.frame()
    clicker.step()  # moves
    now[0] += 0.005
    grabber.frame()
    assert sleeps == [pytest.approx(0.015)]
