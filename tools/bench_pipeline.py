"""MOONLIGHT pipeline performance benchmark.

Measures each stage of the 20 Hz loop independently so we can reason about
low-spec feasibility:
  1. mss capture (real screen)
  2. Each perception reader on a synthetic 1280x960 RGB frame
  3. A full perception pass (all readers, as the loop does per tick)
  4. Arduino ping latency (real hardware, if present)

Run:  python tools/bench_pipeline.py
"""
from __future__ import annotations

import os
import statistics
import sys
import time

import numpy as np

# The repo root is the `moonlight` package; its PARENT dir must be on the path.
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PKG_PARENT)


# --- Region layout (absolute pixels, 1280x960 canvas) ---------------------
HP_REGION = (100, 500, 100, 20)
MP_REGION = (100, 525, 100, 20)
BATTLE_REGION = (1100, 700, 156, 440)
MINIMAP_REGION = (1050, 100, 106, 109)
FLOOR_REGION = (1180, 210, 40, 20)


def bench_capture(n: int = 60) -> dict:
    import mss

    region = {"top": 100, "left": 200, "width": 1280, "height": 960}
    with mss.MSS() as sct:
        sct.grab(region)
        times = []
        for _ in range(n):
            t0 = time.perf_counter()
            sct.grab(region)
            times.append((time.perf_counter() - t0) * 1000.0)
    return {
        "median_ms": statistics.median(times),
        "p99_ms": sorted(times)[max(0, int(0.99 * len(times)) - 1)],
        "fps": 1000.0 / statistics.median(times),
    }


def make_frame() -> np.ndarray:
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 60, size=(960, 1280, 3), dtype=np.uint8)
    frame[500:520, 100:200, 0] = 241
    frame[500:520, 100:200, 1] = 97
    frame[525:545, 100:200, 2] = 218
    frame[700:900, 1100:1256, :] = 20
    rng2 = np.random.default_rng(1)
    for y in range(0, 220, 22):
        for _ in range(10):
            x = int(rng2.integers(10, 140))
            frame[700 + y + int(rng2.integers(0, 15)), 1100 + x] = 240
    frame[100:209, 1050:1156, :] = rng2.integers(80, 130, size=(109, 106, 3)).astype(np.uint8)
    frame[150:160, 1100:1110, :] = [255, 255, 255]
    return frame


def bench(name, fn, *args, reps=200, **kwargs):
    fn(*args, **kwargs)
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn(*args, **kwargs)
        times.append((time.perf_counter() - t0) * 1000.0)
    return {
        "name": name,
        "median_ms": statistics.median(times),
        "p99_ms": sorted(times)[max(0, int(0.99 * len(times)) - 1)],
    }


def main() -> None:
    print("=== MOONLIGHT pipeline benchmark (20 Hz = 50 ms budget) ===\n")

    cap = None
    try:
        cap = bench_capture()
        print(f"[1] mss capture 1280x960 : median {cap['median_ms']:.2f} ms   "
              f"p99 {cap['p99_ms']:.2f} ms   (~{cap['fps']:.0f} FPS)")
    except Exception as e:  # noqa: BLE001
        print(f"[1] mss capture FAILED  : {e}")

    from moonlight.perception.battle import BattleReader
    from moonlight.perception.minimap import MinimapReader
    from moonlight.perception.vitals import VitalsReader

    frame = make_frame()
    print(f"\n[2] Perception readers on synthetic {frame.shape[1]}x{frame.shape[0]} RGB:")
    results = {}

    battle = BattleReader(battle_region=BATTLE_REGION)
    results["battle_list"] = bench("battle_list", battle.read_battle_list, frame)

    vitals = VitalsReader(hp_region=HP_REGION, mp_region=MP_REGION)
    results["vitals"] = bench("vitals", vitals.read_vitals, frame)

    minimap = MinimapReader(minimap_region=MINIMAP_REGION)
    results["minimap"] = bench("minimap", minimap.read_position, frame)

    floor = None
    try:
        from moonlight.perception.floor import FloorReader
        floor = FloorReader(floor_region=FLOOR_REGION)
        results["floor"] = bench("floor", floor.read_floor, frame)
    except Exception as e:  # noqa: BLE001
        print(f"    floor           : (skipped - {type(e).__name__})")

    for key, r in results.items():
        print(f"    {key:<14} median {r['median_ms']:.3f} ms   p99 {r['p99_ms']:.3f} ms")

    def full_pass(f: np.ndarray) -> None:
        battle.read_battle_list(f)
        vitals.read_vitals(f)
        minimap.read_position(f)
        if floor is not None:
            floor.read_floor(f)

    full = bench("FULL_PASS", full_pass, frame)
    print(f"\n[3] Full perception pass  : median {full['median_ms']:.3f} ms   "
          f"p99 {full['p99_ms']:.3f} ms")

    print("\n[4] Arduino serial latency (real hardware):")
    try:
        from moonlight.input import ArduinoInput, find_arduino_port

        if find_arduino_port() is None:
            print("    no Arduino found; skipping")
        else:
            ard = ArduinoInput()
            ard.ping()
            times = []
            for _ in range(30):
                t0 = time.perf_counter()
                ard.ping()
                times.append((time.perf_counter() - t0) * 1000.0)
            ard.close()
            print(f"    ping round-trip    : median {statistics.median(times):.2f} ms   "
                  f"p99 {sorted(times)[max(0, int(0.99 * len(times)) - 1)]:.2f} ms")
    except Exception as e:  # noqa: BLE001
        print(f"    Arduino test error : {e}")

    print("\n=== 50 ms (20 Hz) budget summary (THIS machine) ===")
    if cap:
        print(f"  capture (median)         : {cap['median_ms']:6.2f} ms")
    print(f"  perception pass (median) : {full['median_ms']:6.3f} ms")
    if cap:
        total = cap["median_ms"] + full["median_ms"]
        print(f"  capture + perception     : {total:6.2f} ms   "
              f"-> headroom {50 - total:6.2f} ms")
        if total < 50:
            print("\n  RESULT: fits comfortably in the 50 ms (20 Hz) budget here.")
        else:
            print("\n  RESULT: OVER BUDGET - see recommendations.")


if __name__ == "__main__":
    main()
