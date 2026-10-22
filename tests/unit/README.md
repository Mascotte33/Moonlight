# tests/unit/ - unit tests

Fast, offline unit tests. One `test_<module>.py` per module; all run
on synthetic numpy frames, `FakeInput`, and monkeypatched serial.

## Coverage

| File | Covers |
|------|--------|
| `test_capture.py` | `capture.py` (window capture, DPI, focus) |
| `test_input.py` | `input.py` (InputBackend, FakeInput, protocol encoder) |
| `test_state.py` | `state.py` (WorldState) |
| `test_calibrate.py` | `calibrate.py` (regions, landmark, save/load) |
| `test_loop.py` | `loop.py` (20 Hz loop, priority machine) |
| `test_safety.py` | `safety.py` (rate limiter, F12 kill switch) |
| `test_wiring.py` | `wiring.py` (bot assembly, `build_loop`) |
| `test_check_setup.py` | `tools/check_setup.py` (missing region gives FAIL) |
| `test_vitals.py` | `perception/vitals.py` (HP/MP bar reading) |
| `test_battle.py` | `perception/battle.py` (battle list, selection) |
| `test_minimap.py` | `perception/minimap.py` (player position, radar scroll) |
| `test_floor.py` | `perception/floor.py` (z-level template matching) |
| `test_healing.py` | `decision/healing.py` (emergency/cure/normal, cooldowns) |
| `test_targeting.py` | `decision/targeting.py` (attack/switch/flee, player safety) |
| `test_buffs.py` | `decision/buffs.py` (missing/expiring recast, priorities) |
| `test_alarms.py` | `decision/alarms.py` (HP drop, low HP, player, PM alarms) |
| `test_attack_spells.py`, `test_cavebot.py`, `test_looting.py`, `test_refill.py` | the matching `decision/` modules |
| `test_cursor.py`, `test_sprites.py`, `test_logocr.py` | the matching `perception/` modules |
| `test_clicker.py`, `test_arduino_input.py` | `hardware/clicker.py`, `ArduinoInput` |
| `test_config.py`, `test_autofind.py`, `test_loop_wiring.py`, `test_loop_factory.py`, `test_scripts.py`, `test_golden_labels.py` | `setup/` and `bot/` pieces |
| `test_gui_*.py` | the GUI tabs and theme (need `customtkinter`) |

## Conventions

- Tests must run offline. Never open a real window, port or display.
- New module: new `test_<module>.py` here. A module is not done without it.
- Do not weaken assertions to hide bugs (for example the loop kill-switch test).
- `test_loop.py` style tests drive the loop with a `FakeInput`-backed `GameLoop`. Suite overview: [../README.md](../README.md).
