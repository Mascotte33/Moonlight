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
| `arduino-cli/` | Bundled `arduino-cli.exe` used to flash `firmware/moonlight` (see `firmware/README.md`). |

## Golden-frame labeling

| File | Purpose |
|------|---------|
| `label_golden.py` | Interactively label golden screenshots for perception regression tests. `label` = one image, `batch` = whole directory. Writes label JSON into `tests/golden/`. Run: `python tools/label_golden.py --help` |

## Running

These are tools, not the bot. Smoke-test directly, e.g.:

```bash
python tools/arduino_verify.py          # is the Arduino alive?
python tools/label_golden.py --help     # golden labeling
```

They are exercised indirectly by `tests/unit` and `tests/perf`.
