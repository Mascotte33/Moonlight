# instructions.md — `tests/unit/` (unit tests)

**Read this before touching anything in this folder.**
Parent doc: `tests/instructions.md`.

## What this is
Fast, offline unit tests. Each module in the package has a `test_<module>.py`.

## Status
| File | Covers | Status |
|------|--------|--------|
| `test_capture.py` | `capture.py` | ✅ |
| `test_input.py` | `input.py` | ✅ |
| `test_state.py` | `state.py` | ✅ |
| `test_calibrate.py` | `calibrate.py` | ✅ |
| `test_loop.py` | `loop.py` | ✅ |
| `test_ui.py` | `ui.py` | ✅ |
| `test_config.py` | `config.py` | ✅ |
| `test_cli.py` | `cli.py` (dispatch + headless `build_loop` smoke) | ✅ |
| `test_loop_wiring.py` | `loop.py` decision-module wiring | ✅ |

**Not yet present:** tests for `perception/`, `decision/`, `scripts.py`.

## Agent notes
- Tests MUST run offline: `FakeInput`, synthetic numpy frames, mocked serial.
- New module → new test file here. Update `tests/instructions.md` when adding.
- Don't weaken assertions to hide bugs (e.g. the loop kill-switch test).
- `test_ui.py` drives `MoonlightUI` with scripted input lines and a
  `FakeInput`-backed `GameLoop` (no real stdin, screen, or Arduino).
