# MOONLIGHT Pipeline Performance Profile

**Goal:** run the full loop at 20 Hz (50 ms/cycle) on a low-spec laptop.

## Architecture (what the code actually does)
- **One thread.** `cli.build_loop` builds a single `GameLoop`; every tick runs
  `perceive -> decide -> act` in order on the loop thread (`make_perceive` in
  `moonlight/cli.py`). There is **no** separate perception thread: a slow
  capture or perception pass delays that tick's input. (Only the F12 monitor
  and the Arduino heartbeat run on their own daemon threads.)
- **Capture** (mss/GDI, optional dxcam): `ScreenCapture.get_frame` grabs the
  **full client window** at its native size (e.g. 2560x1369 on the dev
  machine), once per tick for perception.
- **Perception on the tick**: vitals, battle list, minimap and floor only, each
  working on a small calibrated region crop of that frame. Sprites, server-log
  OCR, cursor and loot readers are *not* on the per-tick path.
- **Extra captures**: the clicker (cavebot minimap/tile clicks, looting) grabs a
  fresh frame per verify step — up to `max_iters + 1` grabs in one tick — and
  looting grabs one to look for the loot window. Cavebot position comes from
  `WorldState` (no extra grab).
- **Decision**: pure Python on a few integers/strings — microseconds.
- **Input / Arduino**: synchronous serial commands on the tick thread (ACK wait
  up to the read timeout); every command pays one token of the 10/s wire rate
  limit (`RateLimitedInput`, F-20).

## Measured on build machine (Ryzen 7 7800X3D, RTX 4090)
Run: `python tools/bench_pipeline.py`

| Stage                          | Median  | p99    | Notes                    |
|--------------------------------|---------|--------|--------------------------|
| mss capture 1280x960 region    | 6.8 ms  | 8.2 ms | benchmark region, **not** the production full-window grab |
| perception pass (all readers)  | 0.15 ms | 0.54 ms| synthetic 1280x960 frame |
| Arduino ping round-trip        | 1.0 ms  | 2.0 ms | real Leonardo on COM5    |

The production full-window capture (2560x1369) has **not been measured** yet;
expect it to be several times the 1280x960 figure (roughly 15–30 ms with mss),
and it is repeated for every extra clicker/loot grab in the same tick.

## Low-spec laptop expectation
Capture dominates, and it is serial with input on the single loop thread.
Ticks with clicker activity can take a multiple of the capture time; heals in
those ticks wait for it.

## If it is too slow on your laptop
1. **Capture only needed regions** instead of the full window (biggest win).
2. **Lower the loop rate** to 10 Hz (`LoopConfig.tick_rate`).
3. **Smaller game window** (fewer pixels per full-window grab).
4. Close background apps that fight for CPU/GPU.

## Verify on your laptop
Copy the repo, then:
    python tools/bench_pipeline.py
It prints each stage's timing and the total vs the 50 ms budget (on a
1280x960 region, so add the real full-window capture cost on top).
