# moonlight/ (the package)

The bot itself. Pipeline: `capture -> perception -> WorldState -> decision -> input -> Arduino -> OS`.
The package lives at `<repo>/moonlight/`. Lint, type-check and test from the repo root.
For the project overview see the [root README](../README.md).

## Sub-folders

| Folder | What it holds |
|--------|---------------|
| [bot/](bot/) | 20 Hz loop and priority state machine (`loop.py`), safety (`safety.py`: rate limit, F12 kill), `state.py` (`WorldState`), `wiring.py` (`build_loop`), `scripts.py` (user script runner) |
| [perception/](perception/README.md) | Screen readers (vitals, battle, minimap, floor, loot, cursor, sprites, log OCR) and window capture |
| [decision/](decision/README.md) | Heal, target, buffs, cavebot, loot, refill, alarms, attack spells |
| [hardware/](hardware/) | `input.py` (`InputBackend`, `ArduinoInput`, `FakeInput`, wire encoder) and `clicker.py` (closed-loop `ClickController`) |
| [setup/](setup/) | `config.py` (`BotConfig`, fail-closed), `profile.py` (`ProfileManager`), `calibrate.py` (region calibration), `autofind.py` |
| [gui/](gui/) | customtkinter window with 10 tabs. `python -m moonlight` starts it |
| [profiles/](profiles/README.md) | Calibration profile JSON files |
| `data/` | Packaged default config (`config.json`) |

## How it fits together

- `bot/wiring.py` `build_loop(config, profile, backend, ...)` is the one place the loop is built.
  It runs single-threaded: perceive, decide, act.
- Loop state order: KILL, ALARM, EMERGENCY_HEAL, NORMAL_HEAL, ATTACK, LOOT, REFILL, MOVE, IDLE.
- The 10 commands/s cap is enforced at the wire by `RateLimitedInput`. Every module that sends input uses that same wrapper.
- Kill (F12) is handled on the next tick, so the loop stops one tick after it.
- The config file is `moonlight_config.json`. Its format is in the `config.py` docstring.

## How to test

```bash
python -m pytest tests/ -q
python -m ruff check .
python -m mypy .
```

Use a Python that has `customtkinter` installed. Tests are described in [tests/](../tests/README.md).
Some golden-frame tests skip when the screenshots are missing.

## Do not

- Do NOT create `humanize.py` or `stealth.py` or any anti-detection behavior. They were declined
  and deleted. Reliability-only input timing (documented delays, smooth paths) is fine. Never disguise input.
- A new module ships with its unit tests in `tests/unit/`. Update the folder README when you add it.
- Task numbers (T09, T22 ...) in docstrings come from older sessions and may not match current numbering.
  Trust the code.
