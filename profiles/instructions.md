# instructions.md — `profiles/` (calibration + config profiles)

**Read this before touching anything in this folder.**
Last verified: 2026-10-14.

## What this is
Per-bot configuration and screen-calibration profiles.
- `<name>.json` — calibrated screen regions (from `calibrate.py`), keyed to a
  stable landmark, NOT absolute coordinates.
- Also holds serial port (e.g. `COM5`), baud (115200), and behavior config.

## Status
| Item | Status |
|------|--------|
| Folder | ⚠️ **EMPTY** — no profiles exist yet |
| Sample profile | ⬜ none — first profile is created by `calibrate.py` when a user calibrates |
| `README.md` | ⬜ missing (should document the JSON schema) |

## How to use
- Run calibration (`moonlight/calibrate.py`) to generate `<name>.json` here.
- The bot loads the chosen profile at startup to get region rects + serial port.

## Agent notes
- When `calibrate.py` is finished (it is), have a QA/coder task add a
  `profiles/README.md` documenting the exact JSON schema it writes.
- Do NOT commit real serial ports or machine-specific paths to version control;
  provide a `.example.json` template instead.
