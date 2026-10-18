# instructions.md — `firmware/` (Arduino Leonardo HID)

**Read this before touching anything in this folder.**
Last verified: 2026-10-14.

## What this is
Arduino Leonardo (ATmega32U4) firmware that acts as a USB HID keyboard+mouse,
driven over serial by `moonlight/input.py`. This is the **physical input
layer** — the PC sends wire commands, the board turns them into real OS input.

## Status
| File | Task | Status |
|------|------|--------|
| `moonlight/moonlight.ino` | T06 | ✅ DONE (15.7 KB, compiles clean for Leonardo: 0 errors) |
| `README.md` | — | ✅ protocol, pin map, build/flash instructions |

> `moonlight.ino` lives in a `moonlight/` subdir because arduino-cli requires
> the sketch folder name to match the `.ino` file.

## Verified behavior
- Wire protocol: `K`/`D`/`U` (keys), `M` (mouse), `C` (click), `P` (ping),
  `X` (release all); replies `OK`/`ERR`/`PONG`.
- Kill switch pin 2 (LOW = disarm), status LED pin 13, watchdog releases all
  inputs on timeout.
- ⚠️ Note: watchdog timeout is 10s in code; spec target was 2s. Confirm this
  is acceptable before flashing to hardware.

## How to build / flash
```bash
# arduino-cli is vendored at tools/arduino-cli/arduino-cli.exe (Windows)
tools/arduino-cli/arduino-cli.exe compile --fqbn arduino:avr:leonardo firmware/moonlight
tools/arduino-cli/arduino-cli.exe upload  --fqbn arduino:avr:leonardo --port COMx firmware/moonlight
```

## Agent notes
- **Scope boundary:** standard USB HID device ONLY. No VID/PID spoofing, no
  USB descriptor/serial tampering, no hiding from the OS. (T06.B declined.)
- Any protocol change MUST be mirrored in `moonlight/input.py` (ArduinoInput
  encoder) and its tests. Change both together.
