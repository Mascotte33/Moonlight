"""T40: the bot wiring (build_loop seam) and its headless integration smoke."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from moonlight.bot import wiring
from moonlight.bot.loop import GameLoop, LoopConfig
from moonlight.bot.state import Position, WorldState
from moonlight.decision.buffs import BuffManagerConfig
from moonlight.decision.cavebot import Route, Waypoint
from moonlight.decision.looting import LootingConfig
from moonlight.decision.refill import RefillConfig
from moonlight.hardware.input import FakeInput
from moonlight.setup.calibrate import REGIONS, CalibrationProfile, Region
from moonlight.setup.config import BotConfig, CavebotConfig, ConfigError


def make_profile() -> CalibrationProfile:
    p = CalibrationProfile(landmark=(100, 100))
    for i, name in enumerate(REGIONS):
        p.set_region(name, Region(10 + 40 * i, 20, 30, 10))
    return p


def write_profile(d: Path, name: str = "default") -> None:
    d.mkdir(parents=True, exist_ok=True)
    make_profile().save(d / f"{name}.json")



def test_check_module_regions_reports_missing() -> None:
    """F-11: check_module_regions names the missing region(s) per enabled module."""
    profile = make_profile()
    del profile.regions["game_grid"]  # break cavebot only
    cfg = replace(
        BotConfig.default(),
        cavebot=CavebotConfig(Route("hunt", waypoints=[Waypoint(100, 100, 7)])),
    )
    report = wiring.check_module_regions(profile, cfg)
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
    report = wiring.check_module_regions(profile, cfg)
    assert report["cavebot"] == []
    assert all(missing == [] for missing in report.values())



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

    loop = wiring.build_loop(
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

    cache = wiring.FrameCache(provider)
    first = cache()  # nothing cached yet: captures
    assert cache() is first and len(calls) == 1
    fresh = cache.refresh()
    assert cache() is fresh and len(calls) == 2


def test_capture_regions_lists_tick_regions_or_falls_back_to_full() -> None:
    profile = make_profile()
    cfg = BotConfig.default()
    names = ("battle_list", "floor_indicator", "hp_bar", "minimap", "mp_bar")
    assert wiring.capture_regions(profile, cfg) == [
        profile.get_region(n).to_tuple for n in names
    ]
    walking = replace(cfg, cavebot=object(), walk_keys=True)  # type: ignore[arg-type]
    grid = profile.get_region("game_grid").to_tuple
    assert grid in (wiring.capture_regions(profile, walking) or [])
    del profile.regions["minimap"]
    assert wiring.capture_regions(profile, cfg) is None  # uncalibrated: full grab


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

    with caplog.at_level(logging.WARNING, logger="moonlight.bot.wiring"):
        wiring.build_loop(
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



# -- map matching wiring --------------------------------------------------------


def test_start_seeds_minimap_reader(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """make_perceive passes the last known position to MinimapReader."""
    from moonlight.bot.state import Position

    captured: dict[str, object] = {}

    class _FakeMinimap:
        def __init__(self, profile: CalibrationProfile, **kwargs: object) -> None:
            captured.update(kwargs)

        def read_position(self, frame: np.ndarray) -> object:
            return None

    monkeypatch.setattr("moonlight.perception.minimap.MinimapReader", _FakeMinimap)
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    wiring.make_perceive(
        WorldState(), make_profile(), lambda: frame, start=Position(100, 200, 7), map_dir=tmp_path
    )
    assert captured["initial_position"] == Position(100, 200, 7)


def test_lost_minimap_clears_state_position(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-17: a lost tracker publishes "unknown" (0,0,0), not a stale position."""
    from moonlight.bot.state import Position

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
    wiring.make_perceive(state, make_profile(), lambda: frame)()
    assert state.screen_ok
    assert state.position == Position(0, 0, 0)



def test_tick_grabber_folds_the_cursor_read_into_one_grab_per_tick() -> None:
    """P2 + P5: a clicking tick still makes ONE screen grab (regions + cursor ROI)."""
    from moonlight.hardware.clicker import ClickController
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
    grabber = wiring.TickGrabber(
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


def test_cavebot_starts_at_start_index() -> None:
    route = Route("hunt", waypoints=[Waypoint(100, 100, 7), Waypoint(101, 100, 7)])
    cfg = replace(
        BotConfig.default(),
        loop=LoopConfig(enable_f12_monitor=False),
        cavebot=CavebotConfig(route, start_index=1),
    ).validate()
    loop = wiring.build_loop(
        cfg, make_profile(), FakeInput(), frame_provider=lambda: np.zeros((600, 800, 3), np.uint8)
    )
    assert loop.modules.cavebot is not None
    assert loop.modules.cavebot.route.current == 1


def test_start_index_past_the_end_is_refused() -> None:
    route = Route("hunt", waypoints=[Waypoint(100, 100, 7)])
    cfg = replace(BotConfig.default(), cavebot=CavebotConfig(route, start_index=1))
    with pytest.raises(ConfigError, match="outside 1..1"):
        cfg.validate()


# -- live apply ----------------------------------------------------------------


def _live_loop(cfg: BotConfig, state: WorldState | None = None) -> GameLoop:
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    return wiring.build_loop(
        cfg, make_profile(), FakeInput(), frame_provider=lambda: frame, state=state
    )


def test_reconfigure_swaps_only_changed_modules() -> None:
    cfg = replace(BotConfig.default(), loop=LoopConfig(enable_f12_monitor=False))
    loop = _live_loop(cfg)
    old = loop.modules
    assert cfg.healing is not None and loop.reconfigure is not None
    new = replace(cfg, healing=replace(cfg.healing, normal_hp_percent=55))
    assert loop.reconfigure(new)
    assert loop.modules.healing is not old.healing
    assert loop.modules.targeting is old.targeting
    assert loop.modules.alarms is old.alarms


def test_reconfigure_refuses_what_needs_a_restart() -> None:
    cfg = replace(BotConfig.default(), loop=LoopConfig(enable_f12_monitor=False))
    loop = _live_loop(cfg)
    old = loop.modules
    assert loop.reconfigure is not None
    assert not loop.reconfigure(replace(cfg, loop=LoopConfig(tick_rate=5)))
    assert not loop.reconfigure(replace(cfg, looting=LootingConfig()))  # reads a new region
    assert not loop.reconfigure(replace(cfg, serial_port="COM9"))
    assert loop.modules is old


def test_new_route_starts_at_its_nearest_waypoint() -> None:
    near = Route("b", waypoints=[Waypoint(100, 100, 7), Waypoint(200, 200, 7)])
    cfg = replace(
        BotConfig.default(),
        loop=LoopConfig(enable_f12_monitor=False),
        cavebot=CavebotConfig(Route("a", waypoints=[Waypoint(1, 1, 7)])),
    )
    state = WorldState()
    loop = _live_loop(cfg, state)
    with state.lock():
        state.position = Position(199, 201, 7)
    assert loop.reconfigure is not None and loop.reconfigure(
        replace(cfg, cavebot=CavebotConfig(near))
    )
    assert loop.modules.cavebot is not None
    assert loop.modules.cavebot.route.name == "b"
    assert loop.modules.cavebot.route.current == 1


def test_cavebot_on_or_off_keeps_the_bot_running() -> None:
    off = replace(BotConfig.default(), cavebot=None)
    on = replace(off, cavebot=CavebotConfig(Route("hunt", waypoints=[Waypoint(1, 1, 7)])))
    assert not wiring.needs_restart(off, on)
    assert not wiring.needs_restart(on, off)
    assert wiring.needs_restart(off, replace(off, looting=LootingConfig()))
    assert not wiring.needs_restart(replace(off, looting=LootingConfig()), off)
