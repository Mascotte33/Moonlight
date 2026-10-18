# instructions.md — `tests/` (test suite)

**Read this before touching anything in this folder.**
Last verified: 2026-10-14.

## What this is
The test suite. Structure:
- `unit/` — fast, offline unit tests (synthetic frames, `FakeInput`, mocked serial).
- `replay/` — golden-frame replay tests (planned, not yet present).
- `hil/` — HIL suite (T18): latency, click accuracy, serial-loss release. Offline via `mock_serial.py` (`MockBus`/`MockSerial`, drop/re-plug); real device via `tools/hil_harness.py --real` (skips if absent).
- `golden/` — golden-frame dataset + `labels.json` (planned, not yet present).

## Status
| Item | Status |
|------|--------|
| `unit/test_capture.py` | ✅ DONE (8.4 KB) |
| `unit/test_input.py` | ✅ DONE (6.3 KB) |
| `unit/test_state.py` | ✅ DONE (5.1 KB) |
| `unit/test_calibrate.py` | ✅ DONE (8.9 KB) |
| `unit/test_loop.py` | ✅ DONE (14.9 KB) |
| `unit/test_ui.py` | ✅ DONE (17 KB) |
| `replay/`, `golden/` | ⬜ not created yet |

Current baseline: **339 passed, 4 skipped** (the loop kill-switch test was
fixed in `loop.py` on 2026-10-15).

## How to run
```bash
cd <repo-root>
python -m pytest tests/ -q            # whole suite
python -m pytest tests/unit/test_x.py # single file
python -m pytest -k kill               # filter by keyword
```

## Agent notes
- Every new module ships with its unit tests in `unit/`. No module is "done"
  without tests.
- Unit tests must run **offline** (no live client, no real Arduino) — use
  `FakeInput`, synthetic numpy frames, and monkeypatched serial.
- The loop kill-switch bug was fixed in `loop.py` on 2026-10-15; the suite
  is green. Do not weaken assertions to hide future bugs.
