"""T30: per-module latency report against the 20 Hz (50 ms/tick) budget.

Prints a timing table (run with ``-s``). The per-module report only prints
a WARNING; the budget itself is asserted by
``test_production_tick_within_budget`` (F-15). Perception runs on real golden
frames (skips without them); Tesseract-backed ``logocr`` is fail-closed and
therefore near-free when the ``tesseract`` binary is not installed.
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
import pytest

from moonlight.bot.loop import DecisionModules, GameLoop, LoopConfig
from moonlight.bot.state import Capacity, Monster, Position, WorldState
from moonlight.bot.wiring import make_perceive
from moonlight.decision.alarms import AlarmDecision
from moonlight.decision.buffs import BuffManager
from moonlight.decision.cavebot import Cavebot, Route, RouteFileLoader, Waypoint
from moonlight.decision.healing import HealingDecision
from moonlight.decision.looting import Looting
from moonlight.decision.refill import Refill, RefillConfig
from moonlight.decision.targeting import TargetingDecision
from moonlight.hardware.input import FakeInput
from moonlight.perception import (
    BattleReader,
    CursorReader,
    FloorReader,
    LogOCR,
    LogOCRConfig,
    MinimapReader,
    SpriteDetector,
    VitalsReader,
)
from moonlight.perception.loot import TITLE_TEMPLATE, LootReader, LootWindow
from moonlight.setup.calibrate import CalibrationProfile

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
FRAME_DIRS = (ROOT / "tests" / "golden",)
PROFILE = Path(__file__).resolve().parents[1] / "golden_profile" / "moonlight_user.json"
N_FRAMES = 10
RUNS = 100
BUDGET_MS = 50.0

# Template cuts / regions of the golden client layout (see test_golden_smoke.py).
GAME_GRID = (416, 60, 1442, 1056)
LOG_REGION = (180, 1210, 2020, 135)
SPRITE_CUT = (1105, 556, 64, 64)
CURSOR_CUT = (2520, 20, 16, 16)
LOOT_TITLE_CUT = (0, 0, 120, 16)
LOOT_ITEM_CUT = (10, 40, 20, 20)

Stats = tuple[float, float]  # (mean ms, p95 ms)


def _paths() -> list[Path]:
    for d in FRAME_DIRS:
        found = sorted(d.glob("*.png")) if d.is_dir() else []
        if found:
            return found[:N_FRAMES]
    return []


def _measure(fn: Callable[[int], object]) -> Stats:
    """Call ``fn(i)`` RUNS times; return (mean, p95) in milliseconds."""
    samples: list[float] = []
    for i in range(RUNS):
        t0 = time.perf_counter_ns()
        fn(i)
        samples.append((time.perf_counter_ns() - t0) / 1e6)
    return statistics.fmean(samples), sorted(samples)[int(RUNS * 0.95) - 1]


def _cut(rgb: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = rect
    return cv2.cvtColor(rgb[y : y + h, x : x + w], cv2.COLOR_RGB2GRAY).copy()


def _rows(title: str, rows: dict[str, Stats]) -> list[str]:
    return [f"{title} (mean / p95, ms):"] + [
        f"  {name + ':':<12} {m:.2f} / {p:.2f}" for name, (m, p) in rows.items()
    ]


def _decision_stats(tmp: Path) -> dict[str, Stats]:
    clock = time.monotonic
    ws = WorldState()
    with ws.lock():
        ws.screen_ok = ws.client_focused = True
        ws.hp_percent, ws.mp_percent = 55.0, 80.0
        ws.monsters = [Monster("Wolf", 80.0), Monster("Bear", 100.0)]
        ws.selected_target = Monster("Wolf", 80.0)
        ws.capacity = Capacity(weight=95.0, max_weight=100.0)
    frame = np.zeros((64, 64, 3), np.uint8)
    inp = FakeInput()

    healing = HealingDecision()
    targeting = TargetingDecision()
    buffs = BuffManager()
    alarms = AlarmDecision()

    with ws.lock():
        ws.position = Position(100, 100, 7)
    route = Route(name="r", waypoints=[Waypoint(10_000, 100, 7)])
    cavebot = Cavebot(
        route,
        (0, 0, 100, 100),
        mock.Mock(),
        ws,
        game_region=(0, 0, 150, 110),
        input_backend=inp,
        clock=clock,
    )
    cavebot.start()

    reader = mock.Mock()
    reader.read_loot_window.return_value = LootWindow(open=True)
    looting = Looting(reader, mock.Mock(), inp, ws, frame_provider=lambda: frame)

    (tmp / "refill.json").write_text(
        json.dumps(
            {
                "name": "refill",
                "waypoints": [
                    {"x": 100, "y": 100, "z": 7},
                    {"action": "label", "label": "storage"},
                    {"x": 100, "y": 100, "z": 7},
                    {"action": "label", "label": "farming"},
                ],
            }
        )
    )
    refill = Refill(
        RefillConfig(routes_dir=tmp), cavebot, RouteFileLoader(), looting, ws, inp
    )

    def loot_step(_: int) -> None:
        looting.on_target_died("Wolf")
        looting.step(ws)

    return {
        "healing": _measure(lambda _: healing.decide(ws.snapshot())),
        "targeting": _measure(lambda _: targeting.decide(ws.snapshot())),
        "buffs": _measure(lambda _: buffs.track(ws.snapshot())),
        "alarms": _measure(lambda _: alarms.decide(ws.snapshot())),
        "cavebot": _measure(lambda _: cavebot.step()),
        "looting": _measure(loot_step),
        "refill": _measure(lambda _: refill.should_refill(ws.snapshot())),
    }


def test_timing_report(tmp_path: Path) -> None:
    paths = _paths()
    if not paths or not PROFILE.is_file():
        pytest.skip("no golden frames or calibration profile")
    frames = []
    for p in paths:
        bgr = cv2.imread(str(p))
        assert bgr is not None, p.name
        frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    first = frames[0]
    profile = CalibrationProfile.load(PROFILE)

    log, log_cfg = LogOCR(), LogOCRConfig(log_region=LOG_REGION, enabled=True)
    floor = FloorReader(profile)
    minimap = MinimapReader(profile, floor_reader=floor)
    readers: dict[str, Callable[[np.ndarray], object]] = {
        "vitals": VitalsReader(profile).read_vitals,
        "battle": BattleReader(profile).read_battle_list,
        "minimap": minimap.read_position,
        "floor": floor.read_floor,
        "cursor": CursorReader(
            cursor_region=(2480, 0, 80, 80),  # small calibrated-style region around the cut,
            cursor_templates=_cut(first, CURSOR_CUT),
        ).read,
        "sprites": SpriteDetector(
            game_grid_region=GAME_GRID, monster_templates={"probe": [_cut(first, SPRITE_CUT)]}
        ).detect,
        "logocr": lambda f: log.read(f, log_cfg),
        "loot": LootReader(
            {TITLE_TEMPLATE: _cut(first, LOOT_TITLE_CUT), "probe": _cut(first, LOOT_ITEM_CUT)},
            region=(0, 0, 400, 400),
        ).read_loot_window,
    }
    perception = {
        name: _measure(lambda i, read=read: read(frames[i % len(frames)]))  # type: ignore[misc]
        for name, read in readers.items()
    }
    decision = _decision_stats(tmp_path)

    # Full tick: capture (preloaded frame) -> perceive -> decide -> act.
    ws = WorldState()
    inp = FakeInput()
    loop = GameLoop(
        ws,
        inp,
        LoopConfig(enable_f12_monitor=False),
        sleep_fn=lambda _dt: None,
        modules=DecisionModules(
            healing=HealingDecision(spell_combos={"exura gran": "F1", "exura": "F2"}),
            targeting=TargetingDecision(),
            buffs=BuffManager(),
            alarms=AlarmDecision(),
        ),
        perceive=make_perceive(ws, profile, lambda: frames[0]),
    )

    def one_tick(_: int) -> None:
        loop._stop_event.clear()  # a stopped loop (e.g. !screen_ok) is re-armed
        loop.tick()

    tick_mean, tick_p95 = _measure(one_tick)

    perc_sum = sum(p for _, p in perception.values())
    print("\n=== MOONLIGHT TIMING REPORT ===")
    print("\n".join(_rows("Perception modules", perception)))
    print()
    print("\n".join(_rows("Decision modules", decision)))
    print()
    mark = "OK" if tick_p95 < BUDGET_MS else "WARNING: over budget"
    print(
        f"Full tick: {tick_mean:.1f}ms mean, {tick_p95:.1f}ms p95 "
        f"(budget: {BUDGET_MS:.0f}ms) {mark}"
    )
    print(f"Perception p95 sum: {perc_sum:.1f}ms (budget: {BUDGET_MS:.0f}ms)")
    if perc_sum >= BUDGET_MS:
        worst = sorted(perception.items(), key=lambda kv: kv[1][1], reverse=True)[:3]
        print(
            "WARNING: perception over budget; bottlenecks: "
            + ", ".join(f"{n} ({p:.1f}ms)" for n, (_, p) in worst)
        )
    assert tick_mean > 0.0


def test_production_tick_within_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-15/P7: the real tick -- ``build_loop`` + ``make_perceive`` + a real
    ``ScreenCapture.get_frame`` of the calibrated regions at native resolution
    + a cavebot step whose clicker converges and clicks -- has p95 < 50 ms,
    with exactly one screen grab per tick (``TickGrabber`` folds the cursor
    read into it).

    Perception sees a golden frame (real client content); the screen grab is
    real but its pixels are discarded, so the capture cost is measured even
    without a Tibia window (the grab then targets the primary screen at the
    golden frame size). Skips without golden frames or a display.
    """
    paths = _paths()
    if not paths or not PROFILE.is_file():
        pytest.skip("no golden frames or calibration profile")
    from moonlight.bot.wiring import TickGrabber, build_loop, capture_regions
    from moonlight.perception.capture import ScreenCapture
    from moonlight.setup.calibrate import Region
    from moonlight.setup.config import BotConfig

    bgr = cv2.imread(str(paths[0]))
    assert bgr is not None, paths[0]
    frames = [cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)]
    height, width = frames[0].shape[:2]
    cap = ScreenCapture()
    try:
        cap.capture_region(0, 0, 8, 8)
    except Exception as exc:  # noqa: BLE001 - headless CI / no desktop session
        cap.close()
        pytest.skip(f"no display: {exc}")
    if cap.get_window_rect() is None:
        cap.get_window_rect = lambda: (0, 0, width, height)  # type: ignore[method-assign]

    class _Here:  # a still frame matches one map spot
        def match(self, *_: object) -> tuple[int, int]:
            return (1000, 1000)

    monkeypatch.setattr("moonlight.bot.wiring._world_map", lambda *_: _Here())
    profile = CalibrationProfile.load(PROFILE)
    profile.regions.setdefault("game_grid", Region(*GAME_GRID))
    # Worst case first read: search the whole window (walk clicks hit the minimap).
    profile.regions.setdefault("cursor", Region(0, 0, width, height))
    # A synthetic cursor (no real templates until T33), drawn into the frame
    # the clicker reads at the FakeInput's virtual cursor, so clicks converge.
    cursor_tpl = np.zeros((11, 11), dtype=np.uint8)
    cursor_tpl[:, 5] = cursor_tpl[5, :] = 255
    cursor_tpl[1, 1], cursor_tpl[9, 9], cursor_tpl[3, 7] = 250, 200, 170
    (tmp_path / "templates" / "cursor").mkdir(parents=True)
    cv2.imwrite(str(tmp_path / "templates" / "cursor" / "arrow.png"), cursor_tpl)
    fake = FakeInput(cursor=(width // 2, height // 2))
    shown = frames[0].copy()
    drawn: list[int] = []
    routes = tmp_path / "routes"
    routes.mkdir()
    (routes / "far.json").write_text(
        # One unreachable waypoint per floor: whatever floor the golden frames
        # read, the route resyncs (never "lost") and the clicker keeps working.
        json.dumps(
            {"name": "far", "waypoints": [{"x": 10_000, "y": 100, "z": z} for z in range(16)]}
        )
    )
    config = BotConfig.from_dict(
        {
            "profile": "moonlight_user",
            "hotkeys": {"exura gran": "F1", "exura": "F2"},
            "routes_dir": str(routes),
            "templates_dir": str(tmp_path / "templates"),
            "loop": {"enable_f12_monitor": False},
            "modules": {
                "healing": {"enabled": True},
                "targeting": {"enabled": True},
                "alarms": {"enabled": True},
                "cavebot": {"enabled": True, "route": "far"},
                "looting": {"enabled": False},
                "refill": {"enabled": False},
                "buffs": {"enabled": False},
            },
        }
    )
    regions = capture_regions(profile, config)
    # The real production grabber (one grab per tick, cursor read folded in).
    # Ticks run back to back here, so its settle wait is skipped: at 20 Hz
    # the 50 ms tick gap already covers it.
    grabber = TickGrabber(cap, regions, config.clicker.settle_ms, sleep_fn=lambda _s: None)
    grabs = [0, 0]  # [screen grabs, cursor reads]
    real_get_frame = cap.get_frame

    def counted_get_frame(regions: Sequence[tuple[int, int, int, int]] | None = None) -> np.ndarray:
        grabs[0] += 1
        return real_get_frame(regions)

    cap.get_frame = counted_get_frame  # type: ignore[method-assign]

    def frame_provider() -> np.ndarray:
        grabber.frame()  # real grab, timed with the tick
        # One golden frame every tick: frames from different moments jump HP,
        # and the resulting ALARM/MOVE flapping would cancel every click.
        return shown

    def roi_provider(rect: tuple[int, int, int, int]) -> np.ndarray:
        grabs[1] += 1
        grabber.roi(rect)  # served from the tick grab (no second grab)
        if drawn:  # erase the previous cursor, draw it where FakeInput has it
            ox, oy = drawn
            shown[oy - 5 : oy + 6, ox - 5 : ox + 6] = frames[0][oy - 5 : oy + 6, ox - 5 : ox + 6]
        x, y = fake.cursor_position
        shown[y - 5 : y + 6, x - 5 : x + 6] = cursor_tpl[:, :, None]
        drawn[:] = [x, y]
        return shown

    loop = build_loop(
        config,
        profile,
        fake,
        frame_provider=frame_provider,
        sleep_fn=lambda _dt: None,
        alert=None,
        roi_provider=roi_provider,
        on_clicker=grabber.attach,
    )

    def one_tick(_: int) -> None:
        loop._stop_event.clear()  # a stopped loop (e.g. !screen_ok) is re-armed
        loop.tick()

    try:
        mean, p95 = _measure(one_tick)
        n_grabs, n_reads = grabs
        clicks = fake.sent_lines.count("C L")
        grab_mean, grab_p95 = _measure(lambda _: cap.get_frame(regions))
    finally:
        cap.close()
    print(
        f"\nProduction tick: {mean:.1f}ms mean, {p95:.1f}ms p95 "
        f"(capture {grab_mean:.1f}/{grab_p95:.1f}ms, "
        f"{'regions' if regions else 'full window'}; {n_reads} cursor reads, "
        f"{clicks} clicks, {n_grabs} screen grabs in {RUNS} ticks; "
        f"budget {BUDGET_MS:.0f}ms)"
    )
    assert n_reads > 0, "the cavebot never clicked: the tick under test is not the real one"
    assert n_grabs == RUNS, "a tick grabbed the screen more than once (P1/P5)"
    assert p95 < BUDGET_MS, f"tick p95 {p95:.1f}ms over the {BUDGET_MS:.0f}ms budget"
