# instructions.md — `firmware/moonlight/` (Arduino sketch)

**Read this before touching anything in this folder.**
Parent doc: `../instructions.md`.

## What this is
The Arduino sketch directory. **The folder name must match the `.ino` file
name** (`moonlight/` + `moonlight.ino`) — arduino-cli enforces this. Do not
rename.

## Status
| File | Status |
|------|--------|
| `moonlight.ino` | ✅ DONE (T06), compiles clean for Leonardo |

## Agent notes
- Build/flash from the repo root with the vendored CLI:
  `tools/arduino-cli/arduino-cli.exe compile --fqbn arduino:avr:leonardo firmware/moonlight`
- Protocol changes must be mirrored in `moonlight/input.py` + its tests.
- Standard USB HID only — no identity spoofing or hiding (T06.B declined).
