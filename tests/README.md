# tests/

Fast, offline test suite. No live client, no real Arduino, no GPU. Use `FakeInput`, synthetic numpy frames and mocked serial.

## Layout

| Dir | What | Details |
|-----|------|---------|
| `unit/` | One `test_<module>.py` per module | [unit/README.md](unit/README.md) |
| `perf/` | Golden-frame accuracy and per-read timing. Skips until golden data exists | [perf/README.md](perf/README.md) |
| `replay/` | Replays the labeled golden frames through every perception module | [replay/README.md](replay/README.md) |
| `hil/` | Hardware-in-the-loop: click latency, accuracy, serial-loss release. Offline via `mock_serial.py`. Real device: `python tools/hil_harness.py` | [hil/README.md](hil/README.md) |
| `safety/` | Safety invariants (kill switch, fail-closed, rate limit, release-all) | [safety/README.md](safety/README.md) |
| `golden/` | Golden screenshots (`*.png`, git-ignored) and `labels.json`. Labels come from `tools/label_golden.py`. Keep the schema stable. `golden_labels.py` loads them | |
| `golden_profile/` | The calibration the golden screenshots were taken with. Golden tests use it, never your live profile, so recalibrating does not break them | |

Without `tests/golden/*.png` the golden tests skip. That is expected.

## Run

```bash
python -m pytest tests/ -q              # whole suite
python -m pytest tests/unit/test_x.py   # single file
python -m pytest -k kill                # filter by keyword
```

Use a Python that has `customtkinter` installed (the GUI tests need it).

## Do not

- A module is not done without its unit tests in `unit/`.
- Tests must run offline. Never open a real window, port or display.
- Do not weaken assertions to hide bugs.
