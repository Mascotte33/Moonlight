# tests/ - test suite

Fast, offline test suite. Every module ships with unit tests that run on
synthetic frames, `FakeInput`, and mocked serial — no live client, no real
Arduino, no GPU.

## Layout

| Dir | Purpose | Status |
|-----|---------|--------|
| `unit/` | Per-module unit tests (synthetic frames, FakeInput) | ✅ 525 tests |
| `perf/` | Golden-frame accuracy + per-read performance tests | ✅ 12 tests (skip until T08 data) |
| `replay/` | Video-replay tests for every module (T28) | ⬜ planned |
| `hil/` | Human-in-the-loop / real-client tests (T18) | ⬜ planned |

Baseline (2026-10-15): **524 passed, 13 skipped**. All 13 skips are expected:
12 golden-frame tests wait for the T08 screenshot dataset, 1 is a dxcam test
gated on a Windows-only capture path.

## Run

```bash
cd <repo-root>
python -m pytest tests/ -q             # whole suite
python -m pytest tests/unit/test_x.py  # single file
python -m pytest -k kill               # filter by keyword
```

See `unit/README.md` and `perf/README.md` for per-file details.
