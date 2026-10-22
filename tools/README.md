# tools/

Developer/automation helper scripts. **Not part of the runtime package** —
used to prepare assets (golden frames, calibration), benchmark, debug, and
manage hardware. Excluded from the strict `ruff`/`mypy` package lint (see
`pyproject.toml`); smoke-test them directly.

## Setup check

| File | Purpose |
|------|---------|
| `check_setup.py` | `[OK]`/`[FAIL]` per check: config, Arduino port, profile, each enabled module's calibration regions, perception imports; Tesseract is a `[WARN]`. Exit 0 = ready. Run: `python tools/check_setup.py [profile]` |

## Arduino

| File | Purpose |
|------|---------|
| `arduino_verify.py` | Verify the Leonardo firmware is live. Auto-detects the Arduino port (VID 0x2341), sends `P`→expect `PONG` and `X`→expect `OK`. Safe — sends no keys/mouse. Run: `python tools/arduino_verify.py` |
| `arduino-cli/` | Bundled `arduino-cli.exe` used to flash `firmware/moonlight` (see [../firmware/README.md](../firmware/README.md)). |

## Golden-frame labeling

| File | Purpose |
|------|---------|
| `label_golden.py` | Interactively label golden screenshots for perception regression tests. `label` = one image, `batch` = whole directory. Writes label JSON into `tests/golden/`. Run: `python tools/label_golden.py --help` |

## Benchmarks and calibration helpers

| File | Purpose |
|------|---------|
| `bench_pipeline.py` | Times each stage of the 20 Hz loop. Budget notes: [../docs/perf_profile.md](../docs/perf_profile.md), [../docs/hw_budget.md](../docs/hw_budget.md) |
| `calibrate_auto.py` | Auto-labels HP%/MP% on golden frames and derives the `hp_bar`/`mp_bar` profile. Options: `--dir`, `--profile` |
| `hil_harness.py` | Runs the HIL checks (click latency, accuracy, serial-loss release) on a real board. Use `--real` for hardware. Offline tests are in [../tests/hil/](../tests/hil/README.md) |

## Running

These are tools, not the bot. Smoke-test directly, e.g.:

```bash
python tools/arduino_verify.py          # is the Arduino alive?
python tools/label_golden.py --help     # golden labeling
python tools/check_setup.py             # is the bot ready to run?
```

Only `check_setup.py` has a unit test (`tests/unit/test_check_setup.py`). The golden labels feed `tests/perf` and `tests/replay`.

## Do not

- Do not commit large binaries. `tools/arduino-cli/` is git-ignored on purpose.
- Keep the scripts working. Do not leave broken ones here.
- Keep the `tests/golden/labels.json` schema stable.
