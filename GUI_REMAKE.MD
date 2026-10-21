# GUI remake

The current GUI looks complete but most of it does not drive the bot. This
document lists what exists today, what is broken, and (once decided) the
remake plan split into **Tab → Functionality → Task**.

## ID scheme

| Level | Format | Example |
|---|---|---|
| Tab | `T<n>` | `T4` Healing |
| Functionality | `T<n>.F<m>` | `T4.F1` HP thresholds |
| Task | `T<n>.F<m>.K<k>` | `T4.F1.K2` wire thresholds into the running loop |

A task is one change that can be built, tested and committed on its own.

## Current state (as of 2026-10-21)

### The core gap: two configs that never meet

- The tabs save to `data/config.json` (`gui/config_bridge.py`, the GUI `BotConfig`).
- **Start bot** builds the loop from `moonlight_config.json` (`setup/config.py`, the real `BotConfig`), in `gui/loop_factory.py`.
- Only four GUI values reach the bot: simulation mode, serial port, baud rate, profile name.
- Healing and Targeting call `to_healing_config()` / `to_targeting_config()` only to **validate**. Nothing passes the result to the loop. Editing a threshold in the GUI changes nothing in the running bot.

### Tabs today

| ID | Tab | Functionalities today | Known gaps |
|---|---|---|---|
| T1 | Dashboard | F1 tick count · F2 creature/monster counts · F3 HP/MP · F4 active modules · F5 battle list · F6 event log tail | read-only, overlaps with Status |
| T2 | Settings | F1 Start / Pause / Kill (F12) · F2 tick rate, window target, input mode · F3 module toggles (Healing, Loot, Cavebot, Overlay) · F4 Save / Load config | F2–F4 write the GUI config the bot ignores |
| T3 | Cavebot | F1 load/save route · F2 waypoint list · F3 refill HP%/MP%, loop route, deposit at full cap | engine is a placeholder (T22 pending) |
| T4 | Healing | F1 HP/MP thresholds for potions/spells · F2 Apply + save | not consumed by the bot |
| T5 | Targeting | F1 max monsters · F2 prefer lowest HP · F3 player protection · F4 attack hotkey · F5 whitelist/blacklist | not consumed; whitelist/blacklist exist only in the GUI |
| T6 | Spell attack | F1 AoE combo + toggle · F2 single-target combo + toggle · F3 mana reserve % | no decision module consumes it |
| T7 | Hardware | F1 serial port / baud · F2 connect / disconnect / PONG check · F3 release all buttons · F4 simulation toggle | works (feeds Start) |
| T8 | Recorder | F1 waypoint from minimap or typed x/y · F2 waypoint type · F3 save route to `data/routes/<name>.json` | routes unused until cavebot lands |
| T9 | Status | F1 HP/MP bars · F2 monsters, target · F3 minimap position, capacity, buffs · F4 uptime, actions sent/dropped | read-only |
| T10 | Diagnostics | F1 tick rate, tick-time percentiles, capture latency · F2 state freshness · F3 module toggles · F4 Reset loop stats | module toggles duplicate T2.F3 |

Missing entirely: Calibrate and Check setup buttons (card `task_21b3ca41`), Buffs and Alarms tabs (the decision modules exist, no GUI), Looting settings.

## Rules every tab follows

1. **One config.** Tabs read and write the real config (`moonlight_config.json`,
   `moonlight/setup/config.py`). The GUI's own copy (`gui/config_bridge.py`
   `BotConfig` and its `*Params` classes, `data/config.json`) is deleted in T0.
2. **One tab pattern.** Each tab file starts with an `OPTIONS` list: one entry
   per option (label, config field, control type). Adding an option = adding one
   entry; removing it = deleting the entry. Buttons and custom widgets go in
   `build()`; live display updates in `on_poll()`; writing to the config in
   `apply()`.
3. **Apply saves.** A tab's Apply button writes to `moonlight_config.json` at
   once. There is no separate Save/Load config in Settings.
4. **Changes apply on Start.** The bot reads its config when it starts; to use
   new values, Stop then Start. Live reload is a later task (T0.F2.K1).

## Workflow

- Work starts on a new branch off `main` after PR #3 (`refactor/modular-layout`) merges.
- T0 goes first; tab tasks build on it.
- One task = one commit; pytest, `ruff check` and `mypy` stay green.

## Task format

Every task is one block, written so an agent can do it without other context:

```
### T3.F2.K1 — Add current waypoint
- What: a button that appends the player's current minimap position to the route.
- Files: moonlight/gui/tabs/cavebot.py, tests/gui/test_cavebot_tab.py
- Done when: clicking it adds a waypoint with the live x/y/z; a test covers it.
```

## Tasks

### T0.F1.K1 — One config
- What: make every tab read/write the real `BotConfig` (`moonlight/setup/config.py`, file `moonlight_config.json`); delete the GUI copy in `gui/config_bridge.py` and `data/config.json`.
- Files: moonlight/gui/config_bridge.py, moonlight/gui/loop_factory.py, moonlight/gui/tabs/*.py, tests/gui/
- Done when: changing a Healing threshold in the GUI, then pressing Start, makes the bot use the new value; a test proves it.

### T0.F1.K2 — Apply saves, remove Save/Load config
- What: each tab's Apply writes `moonlight_config.json`; remove Save config / Load config from the Settings tab.
- Files: moonlight/gui/tabs/settings.py, moonlight/gui/tabs/*.py, tests/gui/
- Done when: Apply on any tab changes the file on disk; Settings has no Save/Load buttons.

### T0.F2.K1 — Live reload (later)
- What: the running loop picks up config changes without Stop/Start.
- Files: moonlight/bot/loop.py, moonlight/bot/wiring.py, moonlight/gui/loop_factory.py
- Done when: changing a Healing threshold while the bot runs takes effect within one tick; a test proves it.

_Tab tasks: the owner lists what each tab needs; they are written up here in the format above._

## Open decisions

- Tab set (merges, new Buffs/Alarms/Looting tabs): left as-is for now.
- Route folder mismatch: the GUI saves routes to `data/routes/`, but `decision/cavebot.py` and `decision/refill.py` read `data/waypoints/`. Settle it in the Cavebot tasks.
