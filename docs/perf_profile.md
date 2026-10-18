# MOONLIGHT Pipeline Performance Profile

**Goal:** run the full loop at 20 Hz (50 ms/cycle) on a low-spec laptop.

## Architecture (why it is low-spec friendly)
- **Capture** (mss/GDI): only stage that touches a large buffer.
- **Perception**: every reader works on **small region crops** (HP bar, battle
  list, minimap), never the full frame. OpenCV cost is therefore tiny.
- **Decision**: pure Python logic on a few integers/strings — microseconds.
- **Input / Arduino**: serial command at ~115200 baud; HID output handled by
  the Leonardo's USB hardware. ~1 ms round-trip, independent of laptop CPU.
- **Threading**: perception writes `WorldState`; the loop reads a
  `snapshot()` and dispatches input separately, so a slow perception pass does
  not block keypresses (only makes the snapshot slightly stale).

## Measured on build machine (Ryzen 7 7800X3D, RTX 4090)
Run: `python tools/bench_pipeline.py`

| Stage                          | Median  | p99    | Notes                    |
|--------------------------------|---------|--------|--------------------------|
| mss capture 1280x960           | 6.8 ms  | 8.2 ms | ~147 FPS                 |
| perception pass (all readers)  | 0.15 ms | 0.54 ms| fast-path; worst-case a few ms |
| Arduino ping round-trip        | 1.0 ms  | 2.0 ms | real Leonardo on COM5    |
| **capture + perception**       | **~7 ms** | —   | **~43 ms headroom at 20 Hz** |

## Low-spec laptop expectation
Capture dominates. On a typical low-spec laptop (e.g. Intel i3 / UHD, 8 GB):
- capture: ~15–40 ms
- perception: ~5–15 ms
- Arduino: ~1 ms
- **expected loop rate: ~10–20 Hz** — plenty to read UI and react in real time.

Older/weaker machines may drop toward ~10 Hz; still functional.

## If it is too slow on your laptop
1. **Capture only needed regions** instead of the full window (biggest win).
2. **Lower the loop rate** to 10 Hz (`GameLoopConfig`).
3. **Smaller capture region / resolution** for the game window.
4. Close background apps that fight for CPU/GPU.

## Verify on your laptop
Copy the repo, then:
    python tools/bench_pipeline.py
It prints each stage's timing and the total vs the 50 ms budget.
