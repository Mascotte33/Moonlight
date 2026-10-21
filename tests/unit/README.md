# tests/unit/ - unit tests

Fast, offline unit tests. One `test_<module>.py` per package module; all run
on synthetic numpy frames, `FakeInput`, and monkeypatched serial.

## Coverage

| File | Covers | Tests |
|------|--------|-------|
| `test_capture.py` | `capture.py` (window capture, DPI, focus) | 23 |
| `test_input.py` | `input.py` (InputBackend, FakeInput, protocol encoder) | 50 |
| `test_state.py` | `state.py` (WorldState) | 17 |
| `test_calibrate.py` | `calibrate.py` (regions, landmark, save/load) | 35 |
| `test_loop.py` | `loop.py` (20 Hz loop, priority machine, kill switch) | 32 |
| `test_vitals.py` | `perception/vitals.py` (HP/MP bar reading) | 35 |
| `test_battle.py` | `perception/battle.py` (battle list, selection) | 36 |
| `test_minimap.py` | `perception/minimap.py` (player position, radar scroll) | 30 |
| `test_floor.py` | `perception/floor.py` (z-level template matching) | 25 |
| `test_healing.py` | `decision/healing.py` (emergency/cure/normal, cooldowns) | 51 |
| `test_targeting.py` | `decision/targeting.py` (attack/switch/flee, player safety) | 36 |
| `test_buffs.py` | `decision/buffs.py` (missing/expiring recast, priorities) | 50 |
| `test_alarms.py` | `decision/alarms.py` (HP drop, low HP, player, PM alarms) | 58 |

**Not yet present:** tests for the still-stub modules `scripts.py` (T27.D),
`decision/cavebot.py` (T22), `decision/looting.py` (T23),
`perception/loot.py` (T23) — they will ship with their implementation tasks.

Baseline (2026-10-15): **524 passed, 1 skipped** (the skip is the dxcam
Windows-only test in `test_capture.py`).

## Conventions

- Tests MUST run offline; never open a real window, port, or display.
- New module → new `test_<module>.py` here. A module is not "done" without it.
- Don't weaken assertions to hide bugs.
