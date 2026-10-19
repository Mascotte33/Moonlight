"""T30: per-module latency report against the 20 Hz (50 ms/tick) budget.

Prints a timing table (run with ``-s``). Timing never fails the build: a
tick over budget only prints a WARNING. Perception runs on real golden
frames (skips without them); Tesseract-backed ``logocr`` is fail-closed and
therefore near-free when the ``tesseract`` binary is not installed.
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

import cv2
import numpy as np
import pytest

from moonlight.calibrate import CalibrationProfile, default_profiles_dir
from moonlight.cli import make_perceive
from moonlight.decision.alarms import AlarmDecision
from moonlight.decision.buffs import BuffManager
from moonlight.decision.cavebot import Cavebot, Route, RouteFileLoader, Waypoint
from moonlight.decision.healing import HealingDecision
from moonlight.decision.looting import Looting
from moonlight.decision.refill import Refill, RefillConfig
from moonlight.decision.targeting import TargetingDecision
from moonlight.input import FakeInput
from moonlight.loop import DecisionModules, GameLoop, LoopConfig
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
from moonlight.state import Capacity, Monster, WorldState

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
FRAME_DIRS = (ROOT / "golde-screenshots", ROOT / "tests" / "golden")
PROFILE = default_profiles_dir() / "moonlight_user.json"
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

    minimap = mock.Mock(minimap_region=(0, 0, 100, 100))
    pos = [0]

    def read_position(_f: object) -> Any:
        pos[0] += 1  # keep moving so the stuck alarm never ends the route
        return mock.Mock(x=100 + pos[0], y=100)

    minimap.read_position = read_position
    floor = mock.Mock()
    floor.read_floor.return_value = 7
    route = Route(name="r", waypoints=[Waypoint(10_000, 100, 7)])
    cavebot = Cavebot(
        route, minimap, floor, mock.Mock(), ws,
        frame_provider=lambda: frame, game_region=(0, 0, 150, 110), clock=clock,
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
