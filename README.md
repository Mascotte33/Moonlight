# Moonlight

Lightweight screen-capture automation bot for Tibia-style game clients.
Designed for **OTS servers** (where bots are allowed) and for running on
**low-spec hardware**: CPU-only, no GPU dependencies, ~50 ms per loop cycle.

- **Capture**: client-window-only screen capture (mss, optional dxcam), DPI-aware
- **Perception**: HP/MP, battle list, minimap + floor, monster sprites, rendered cursor
- **Decision**: priority state machine — healing, targeting, alarms, buffs
- **Input**: HID via an **Arduino Leonardo** over serial (hardware-offloaded, ~1 ms round-trip), closed-loop ±2 px click correction
- **UI**: terminal REPL *and* a 10-tab desktop GUI (customtkinter)

By design (safety boundary): no anti-cheat evasion, no process/window hiding,
no official CipSoft server support. (`humanize.py` / `stealth.py` were
explicitly declined and removed — do not recreate them.)

## Architecture

```
┌───────────┐   ┌──────────────────────────────────────────┐   ┌────────────────────┐
│ capture.py │──▶│ perception/                              │──▶│ decision/          │
│ mss/dxcam  │   │ vitals · battle · minimap · floor        │   │ healing · targeting│
│ region crop│   │ sprites · cursor                         │   │ alarms · buffs     │
└───────────┘   └──────────────────────────────────────────┘   └─────────┬──────────┘
      ▲                                                                  │ actions
      │ 20 Hz loop (loop.py) ── priority queue, rate limit, F12 kill     │
┌─────┴──────────────────────────────────────────────────────────────────▼──┐
│ input.py   InputBackend ABC ── FakeInput (tests) ── ArduinoInput (serial) │
│ clicker.py ClickController: move → read rendered cursor → correct → click │
└───────────────────────────────────────────────────────────────────────────┘
        │                                              ┌──────────────────────┐
        ▼ serial 115200                                │ ui.py  (terminal REPL)│
  Arduino Leonardo (HID)                               │ gui/   (10-tab app)  │
```

The loop reads `WorldState` each cycle; perception operates on **small region
crops, never full frames** — that is what keeps it cheap on weak CPUs.

## Repository layout

| Path | What |
|---|---|
| `capture.py` | Client-window capture, DPI-aware, focus detection |
| `calibrate.py` | Interactive region calibration → `profiles/<name>.json` |
| `state.py` | `WorldState` (client focused, screen ok, perception results) |
| `loop.py` | 20 Hz priority state machine, rate limiting, F12 kill switch |
| `input.py` | `InputBackend` ABC, `FakeInput`, `ArduinoInput` (pyserial, heartbeat, reconnect) |
| `clicker.py` | Closed-loop click: move → read rendered cursor → correct → click (±2 px, fail-closed) |
| `perception/` | `vitals` (HP/MP), `battle` (list + selection), `minimap` (radar), `floor` (16 levels), `sprites` (monsters), `cursor` |
| `decision/` | `healing`, `targeting`, `alarms`, `buffs`, `cavebot` (routing, in progress), `looting` |
| `scripts.py` | User `.py` script loader (waypoint- or condition-triggered) — stub |
| `ui.py` | Terminal REPL: start/stop/kill, profiles, state, log, stats, calibrate |
| `gui/` | 10-tab customtkinter GUI (Dashboard, Settings, Cavebot, Healing, Targeting, Spell, Hardware, Recorder, Status, Diagnostics) |
| `firmware/` | `moonlight.ino` — Arduino Leonardo HID firmware (verified on real HW) |
| `tools/` | Dev scripts: `bench_pipeline.py`, `arduino_verify.py`, `label_golden.py`, dxcam probes |
| `tests/` | unit / golden / hil / perf / replay suites (22 test files, 640 tests) |
| `profiles/` | Calibration profiles (`<name>.json` with screen regions) |
| `data/` | Route files (waypoint JSON written by the GUI Recorder) |
| `PROGRESS.md` | Task tracker (47 tasks: done / in_progress / pending / declined) |

Every folder has an `instructions.md` for agents working in it.

## Requirements

- **Python ≥ 3.12** (developed against 3.14)
- Core: `opencv-python`, `numpy`, `pyserial`, `mss`
- GUI: `customtkinter` (optional, `pip install customtkinter`)
- Optional (Windows): `dxcam` for lower-latency capture
- Hardware (for real input): **Arduino Leonardo** flashed with `firmware/moonlight.ino`

## Quick start

```bash
# 1. Install
pip install -e ".[dev]"          # from the repo root
pip install customtkinter        # only for the GUI

# 2. Flash the Arduino (optional — FakeInput works without it)
#    firmware/moonlight.ino, then: python tools/arduino_verify.py

# 3. Calibrate: draw the screen regions for your client window
#    opens an OpenCV window; click the landmark, drag each region, Esc cancels
cd ..                            # repo's PARENT dir — see note below
python -m moonlight.ui           # → command: calibrate <profile-name>

# 4. Run
python -m moonlight.ui           # terminal bot: start / state / stats / kill
python -m moonlight.gui          # 10-tab GUI (--help, --profile <name>, --no-bot)
```

**Import note:** the repo root *is* the `moonlight` package (flat layout,
`__init__.py` at the root), so `python -m moonlight.*` must be run from the
repo's **parent** directory. `pytest` / `ruff` / `mypy` run from the repo
**root** (pytest's conftest inserts the parent dir on `sys.path` automatically).

## Hardware: Arduino Leonardo

The Leonardo emulates a USB HID keyboard/mouse and is auto-detected by
**VID 0x2341** (no PID pinning). Serial protocol, 115200 baud, one command
per line, replies `OK` / `ERR`:

| Command | Action |
|---|---|
| `K <combo>` | press+release combo (`F1`, `CTRL+F3`, `SPACE`, `a`, …) |
| `D <combo>` / `U <combo>` | hold / release combo |
| `M <dx> <dy>` | relative mouse move (each axis −127..127 per command) |
| `P` | poll — firmware replies `PONG` (1 s heartbeat from host) |
| `X` | test — firmware replies `OK` |

`ArduinoInput` sends the heartbeat, reconnects on loss, and stops the loop on
lost `PONG`. A physical kill pin (F12) is supported for hardware kill-switch
wiring. The firmware is verified on a real Leonardo (auto-detect, heartbeat
3/3, physical ScrollLock keypress).

## Performance budget

The full loop — capture → perception → decision → Arduino input — must fit
**50 ms (20 Hz)** on low-spec hardware. The dev machine (RTX 4090) is *not*
the deployment target. Live check:

```bash
python tools/bench_pipeline.py   # per-reader costs, total vs the 50 ms budget
```

Tuning levers: capture only the needed regions, lower loop rate, smaller
regions (details in `docs/perf_profile.md`).

## Testing & CI

```bash
cd moonlight/        # repo root
ruff check .         # line-length 100, py312, tools/ excluded
mypy .               # strict, py312
pytest -q            # baseline: 640 passed, 13 skipped
```

CI (`.github/workflows/ci.yml`): ruff + mypy + pytest + capture/perception
benchmarks. Test suites: `tests/unit` (everything, offline), `tests/golden`
(real-frame calibration, un-skipped by the golden-set task), `tests/hil`
(hardware-in-the-loop), `tests/perf`, `tests/replay`.

## Calibration & golden set

- `profiles/<name>.json` stores screen regions (HP/MP bar, battle box,
  minimap, floor, cursor) for a given client resolution; calibrated
  interactively or offline from a screenshot (`calibrate <name> --screenshot <path>`).
- The **golden set** — 200 real client frames, 2560×1369, PNG (lossless only —
  no JPEG/lossy WebP, pixel-art colors must survive OCR) — lives in
  `golde-screenshots/` and is **intentionally not committed** (gitignored, ~585 MB).

## Status

Core pipeline is complete and verified end-to-end on real hardware: capture →
perception → decision → 20 Hz loop → closed-loop clicks → Arduino HID. See
`PROGRESS.md` for the full task breakdown (~62 % of the 40 in-scope tasks done).
Remaining: cavebot routing, looting, refill routes, script runner, OCR log
parsing, the golden-set calibration, and the QA/review tail (HIL, replay,
safety, timing, architecture review).

## License

MIT
