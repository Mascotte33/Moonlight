# firmware/

Arduino Leonardo (ATmega32U4) firmware that makes the board act as a USB
HID keyboard + mouse, driven over serial. This is the **physical input layer**
of MOONLIGHT — the PC's `moonlight/hardware/input.py` sends wire commands, and this
firmware turns them into real keyboard/mouse events.

> **Scope note:** The board is a **standard USB HID device**. Per the project
> safety boundary, this firmware does **NOT** spoof its USB identity (VID/PID,
> descriptors, serials) or attempt to hide itself from the OS.

## File layout

| File | Purpose |
|------|---------|
| `moonlight/moonlight.ino` | The sketch. arduino-cli requires the sketch dir to match the `.ino` name, hence the `moonlight/` subdirectory. |

## Wire protocol (115200 baud, one command per line)

The board replies `OK`, `ERR`, or `PONG` (each followed by `\n`).

| Command | Meaning | Reply |
|---------|---------|-------|
| `K <combo>` | Press + release key(s) (e.g. `F1`, `CTRL+F3`, `SPACE`, `a`) | `OK` |
| `D <combo>` | Hold key(s) down (no release) | `OK` |
| `U <combo>` | Release key(s) | `OK` |
| `M <dx> <dy>` | Relative mouse move; **each axis must be −127..127**, else rejected | `OK` / `ERR` |
| `C L` | Left mouse click | `OK` |
| `C R` | Right mouse click | `OK` |
| `P` | Ping | `PONG` |
| `X` | Release **all** inputs | `OK` |
| (empty) | Empty / malformed / unknown line | `ERR` |

Combos use `+` to join simultaneous keys (e.g. `CTRL+SHIFT+F12`). Key tokens
are case-insensitive: `ctrl alt shift meta a-z 0-9 f1-f24 tab enter space
backspace delete insert home end pgup pgdn up down left right esc`.

## Hardware / behavior

- **Kill switch — pin 2 → GND** (optional; unwired = safe). Read as
  `INPUT_PULLUP`. When driven LOW (button pressed) the board **immediately
  releases all held keys and mouse buttons** — an instant physical safety stop.
  It then continues to respond to commands normally.
- **Status LED — pin 13** (on-board). ON while armed/idle; pulses on valid
  commands; blinks on `ERR`.
- **Watchdog.** If no command arrives within the timeout, the board releases
  all inputs once. In production the PC sends `P` every 1 s.
- **Safety:** all inputs are released on disarm (kill pin) and on watchdog
  expiry, so a dead PC or pressed button can never leave a key stuck down.

## Build & flash

Requires `arduino-cli` and the AVR core.

```bash
# One-time setup
arduino-cli core update-index
arduino-cli core install arduino:avr

# Compile (must be 0 errors)
arduino-cli compile --fqbn arduino:avr:leonardo firmware/moonlight

# Upload to the connected board (adjust COM port)
arduino-cli upload --fqbn arduino:avr:leonardo --port COM5 firmware/moonlight
```

On Windows, `arduino-cli` can be obtained from
https://github.com/arduino/arduino-cli/releases. A local copy is kept at
`tools/arduino-cli/` (git-ignored).
