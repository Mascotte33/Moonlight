# instructions.md — root package (`moonlight/`)

**Read this before touching anything in this folder.**
Last verified: 2026-10-14 by orchestrator (after merging two divergent session states).

## What this is
The core `moonlight` package. Pipeline:
`capture -> perception -> WorldState -> decision -> input -> Arduino -> OS`.

**Layout note (important):** the package lives in `moonlight/` under the repo
root (`<repo>/moonlight/__init__.py`). Lint/type-check/test on `.` (the repo root).

## Status (verified by test run + file sizes, not PROGRESS.md)

### DONE (implemented + tested)
| File | Size | What it does | Test |
|------|------|--------------|------|
| `capture.py` | 16 KB | Window capture (mss + optional dxcam), DPI-aware, 20 Hz | `tests/unit/test_capture.py` ✅ |
| `input.py` | 9.6 KB | `InputBackend`, `ArduinoInput` (pyserial), `FakeInput`, protocol encoder | `tests/unit/test_input.py` ✅ |
| `state.py` | 4.4 KB | `WorldState` container (hp/mp/targets/pos/alarms/screen_ok) | `tests/unit/test_state.py` ✅ |
| `calibrate.py` | 22 KB | Region calibration -> `profiles/<name>.json` | `tests/unit/test_calibrate.py` ✅ |
| `loop.py` | 25.7 KB | 20 Hz main loop, priority state machine, F12 kill switch | `tests/unit/test_loop.py` ✅ |
| `ui.py` | 22 KB | Terminal UI: `BotController`, `ProfileSelector`, `StateDisplay`, `LogViewer`, command loop + CLI | `tests/unit/test_ui.py` ✅ |
| `clicker.py` | 7.5 KB | Closed-loop clicking: `ClickController` — move → verify cursor via `CursorReader` → correct → click once (fail-closed) | `tests/unit/test_clicker.py` ✅ |

### STUBS (docstring only — no logic yet)
| File | Task | Note |
|------|------|------|
| scripts.py | T27.D | User .py script loader — **done (2026-10-18)**: ScriptRunner/BotScript/Condition/Trigger, 35 tests |

### ⛔ DECLINED (evasion) — REMOVED
`humanize.py` (T17.B) and `stealth.py` (T27.B) were empty placeholders created
by a prior session. The orchestrator declined these (anti-detection evasion)
and **deleted both files on 2026-10-14**. Do not recreate them.

## How to test / check
```bash
cd <repo-root>
python -m pytest tests/          # 685 pass, 13 skip (golden tests skip until T08 data)
ruff check .                       # clean
mypy .                             # clean (36 files)
```

## ✅ perception/battle.py (T19) — DONE (2026-10-15)
Battle-list reader per docs/ui_layout.md #3: 22 px slots, 156 px content
width, 11 px top padding, grayscale text threshold >150, selected row
detected via 76/166 highlight greys at the 20 px box corners (>=3 of 4).
`BattleReader.read_battle_list(frame, region) -> list[BattleEntry]`;
`entries_to_state(entries)` maps to (monsters, selected_target,
players_on_screen). Names are pluggable via `NameResolver` — no OCR output
is fabricated; empty names until golden data (T08) + templates arrive.
Synthetic-frame unit tests: `tests/unit/test_battle.py` (36 tests, includes
a <15 ms per-read perf check). Golden tests: `tests/perf/test_battle_golden.py`
(skip cleanly until data/golden/ or tests/golden/ + labels.json exist).

## ✅ decision/alarms.py (T25) — DONE (2026-10-15)
Alarm decisions: `AlarmDecision.decide(state) -> AlarmAction | None`.
Detects, in priority order `ALARM_PRIORITY`: `fast_hp_drop` (critical,
heal), `low_hp` (critical, heal), `player_detected` (warning, flee),
`pm_open` (warning, alert_user). Keeps a rolling HP history (default 10
readings) and compares against the reading from ~`fast_hp_drop_window`
(default 1.0 s) ago; `fast_hp_drop_threshold` default 10 HP points;
`low_hp_threshold` default 20%. PM detection is an injectable
`pm_open_check` (never fires without one); unknown HP (0.0) is ignored.
Configurable via `AlarmConfig` (validated fail-fast). Pure module: clock
injectable, no input backend. Unit tests: `tests/unit/test_alarms.py`
(58 tests).

## ✅ perception/loot.py + decision/looting.py (T23) — first pass (2026-10-18)
`LootReader(templates, profile).read_loot_window(frame) -> LootWindow | None`:
title-bar template (`"loot_window"` key) in the calibrated `loot_window`
region, then a 5x5 grid of 34 px slots below it, each matched against the
item templates (count is always 1 for now). No templates/region -> None.
`Looting.step(snap)`: press `loot_hotkey` -> `move_all_hotkey` (if set) ->
`loot_hotkey` to close; gives up after 10 ticks without a window. Free cap
= `100 - capacity.percent`; unknown cap does not block. Per-item drags and
`max_items_per_loot` land in T24. Tests: `tests/unit/test_looting.py` (12).

## ✅ decision/refill.py (T24) — DONE (2026-10-18)
`Refill.should_refill(snap)` (used cap >= `trigger_cap`, route loaded, no
pending loot, no alarm) → `start()` → `step(snap)` per tick. The route
`data/waypoints/<route_name>.json` needs labels `storage` then `farming`;
it is split into two legs that the existing `Cavebot` runs (route swapped
in, restored on finish). DEPOSIT: `storage_hotkey` opens, `deposit_hotkey`
every 1 s until cap <= `stop_cap`, `storage_hotkey` closes; 5 s without a
cap drop → alarm `deposit_failed` (no per-item drags: no backpack reader,
no mouse-hold primitive). Cavebot alarms abort the refill. Not wired into
`loop.py` yet. Tests: `tests/unit/test_refill.py` (12).

## ✅ decision/cavebot.py (T22) — DONE (2026-10-18)
Route format: `data/waypoints/README.md` (JSON, `RouteFileLoader` validates).
`Cavebot.step()` reads x/y from `MinimapReader`, z from `FloorReader`, walks
by clicking the minimap (1 px = 1 tile) via `ClickController`, does
rope/shovel (hotkey + tile click) and ladder (right-click) on the 15x11
game grid, then waits for the floor change. No movement for 5 s → stops,
`alarm = "stuck"`; unexpected floor → resync to next waypoint on that floor
or `alarm = "lost"`. Wire into the loop with
`move_provider=lambda _s: bot.is_active(), move_action=bot.step`.
Unit tests: `tests/unit/test_cavebot.py` (33 tests).

## ✅ clicker.py (T17) — DONE (2026-10-16)
Closed-loop clicking: `ClickController.click_at(target)` first reads the
*rendered* cursor position from the frame with `CursorReader`
(`perception/cursor.py`: grayscale template matching in the calibrated
`cursor` region, fail-closed → `None` when absent), then sends a chunked
HID move (≤ ±127 per axis) and re-reads the cursor up to `max_iters`,
issuing correction moves until the cursor is within `tolerance` px — only
then does it click exactly once (left or right via `ClickSide`). Cursor
not found or non-convergent loop → fail-closed, no click sent.
Unit tests: `tests/unit/test_clicker.py` (17 tests) +
`tests/unit/test_cursor.py` (25 tests), both offline against `FakeInput`
and synthetic frames.

## ✅ decision/targeting.py (T20) — DONE (2026-10-15)
Targeting decisions: `TargetingDecision.decide(state) -> TargetingAction | None`
with `action` in {attack, switch, flee}. Keeps a still-valid selected monster
target; otherwise picks the weakest monster (`prefer_low_hp`, default) or the
first in the battle list. Hard safety rules: never targets players (filters
player entries even if mislabeled), `players_on_screen > 0` -> flee
(`player_protection`, default), monsters > `max_monsters_to_engage` (default 1)
-> flee, empty battle list -> None (idle). Pure module, no input backend.
Unit tests: `tests/unit/test_targeting.py` (36 tests).

## ✅ loop.py kill-switch bug — FIXED (2026-10-15)
`test_run_ticks_at_20hz_until_kill` now passes. The MOVE waypoint step and
the kill switch's release-all now bypass the 10/s rate limiter (both still
gated by client_focused/screen_ok): the waypoint runner is continuous
motion (one step per 20 Hz tick), and an emergency release must not wait
for a token. Kill is now processed on the next tick after activation, so
the loop stops exactly one tick after KILL.

## ✅ ui.py (T27) — DONE (2026-10-15)
Terminal UI with **zero new dependencies** (stdlib only; no GUI toolkit,
no `rich`). `BotController` runs `GameLoop` on a daemon thread (with an
optional `loop_factory` for restarts after kill/stop, since a `GameLoop`
can only run once). `ProfileSelector` wraps `calibrate.CalibrationProfile`
over `profiles/<name>.json`. `MoonlightUI.run()` takes injectable
`input_fn`/`output`, so it is fully testable offline (scripted lines).
CLI: `python -m moonlight.ui` (needs the package importable, e.g.
`PYTHONPATH=<parent-of-repo>` or pytest's conftest path). `main()` uses
`FakeInput` until T16 ships `ArduinoInput`.

## Agent notes
- **Do NOT** implement `humanize.py` or `stealth.py` evasion behavior. See the
  hard boundary in `PROGRESS.md` Notes. Reliability-only input timing is the
  allowed middle ground (documented delays, smooth paths) — never disguise.
- When adding a module, add its unit tests to `tests/unit/` and update this file.
- Task numbers in older READMEs (T09, T11, T13…) are from a prior session and
  may not match current PROGRESS.md numbering — trust the code and this file.
