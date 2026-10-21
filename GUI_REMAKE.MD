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

## Target design (from Tibiaeye)

The remake copies the **look and features** of [Tibiaeye](https://github.com/GGotha/tibiaeye)'s GUI
(screenshots in its `readme/` folder). Its code has no license, so **do not copy
code from it**; build each function in our code, following the rules below.
"Today" says what Moonlight already has: ✅ works, 🟡 exists but not wired to
the bot / incomplete, ❌ missing.

### S — App shell (on every tab)

| ID | Function | Today |
|---|---|---|
| S.F1 | Left sidebar navigation (logo + one entry per tab, active tab highlighted) instead of top tabs | ❌ (top tab bar) |
| S.F2 | Top bar: HP bar + %, MP bar + %, bot state dot + text (Stopped / Running / Paused) | ❌ |
| S.F3 | Top bar: Start ▶ / Pause ⏸ / Stop ■ buttons, usable from any tab | ❌ (only in Settings) |
| S.F4 | Bottom bar: connection dots (Screen, Arduino, Bot) | ❌ |
| S.F5 | Bottom bar: CPU % and memory MB of the bot process, with small bars | ❌ |
| S.F6 | Dark theme: card panels with small uppercase section titles | 🟡 (dark, no cards) |

### T1 — Dashboard

| ID | Function | Today |
|---|---|---|
| T1.F1 | Stat cards: Uptime, Ticks, Creatures, HP % | 🟡 (ticks, creatures, HP; no uptime) |
| T1.F2 | Bot health: one dot per perception reader (capture, vitals, battle, minimap, …), green/red | ❌ |
| T1.F3 | Current task card (Idle / Healing / Attacking / Walking to waypoint N …) | ❌ |
| T1.F4 | Battle list panel | ✅ |
| T1.F5 | Recent events panel | ✅ |

### T2 — Settings

| ID | Function | Today |
|---|---|---|
| T2.F1 | Big Start / Pause / Stop buttons + status text | ✅ |
| T2.F2 | Tick rate (seconds) | 🟡 (not read by the bot) |
| T2.F3 | Window picker (dropdown of open windows) + Refresh | 🟡 (text field) |
| T2.F4 | Module toggles: Healing, Cavebot, Loot (with loot hotkey), Stuck alert (with timeout s), Session logging, Screen overlay, Debug overlay (grid + creatures) | 🟡 (Healing, Loot, Cavebot, Overlay only; not read by the bot) |
| T2.F5 | Server save: enable + save time (CET) | ❌ |
| T2.F6 | Auto-reconnect section (below server save; contents not visible in the screenshot) | ❌ |

### T3 — Cavebot

| ID | Function | Today |
|---|---|---|
| T3.F1 | Route file path + Browse, Reload, Load route | 🟡 (dropdown of saved routes) |
| T3.F2 | Start from waypoint N | ❌ |
| T3.F3 | Loop route toggle | ✅ |
| T3.F4 | Waypoint table (#, type, coordinates) with the current waypoint marked `>` | 🟡 (list, no current marker) |
| T3.F5 | "Current: waypoint N of M" line | ❌ |
| T3.F6 | Add current position as a waypoint (owner request) | ❌ |
| T3.F7 | Refill sub-tab: city; refill when HP potions < N, MP potions < N, capacity < N oz (toggle); refill to HP/MP potion counts and potion types; deposit gold before refill; return-to label | 🟡 (HP%/MP% thresholds and deposit only) |
| T3.F8 | Loot sub-tab (contents not visible in the screenshot) | ❌ |

### T4 — Healing

| ID | Function | Today |
|---|---|---|
| T4.F1 | HP/MP thresholds for potions and spells (no screenshot; from the README) | 🟡 (not read by the bot) |

### T5 — Targeting

| ID | Function | Today |
|---|---|---|
| T5.F1 | Whitelist **or** blacklist mode switch | 🟡 (both lists, no mode) |
| T5.F2 | Creature list editor | 🟡 |
| T5.F3 | Attack nearest creature in range (behaviour, README) | 🟡 (picks first in battle list, or lowest HP) |

### T6 — Spell attack

| ID | Function | Today |
|---|---|---|
| T6.F1 | AoE and single-target spell groups | 🟡 (no decision module uses them) |
| T6.F2 | Mana reserve protection | 🟡 |

### T7 — Hardware

| ID | Function | Today |
|---|---|---|
| T7.F1 | Hardware mode radio with one-line description each. Tibiaeye: Software / Capture card / Arduino + mss / Full hardware. Moonlight only needs **Arduino + mss** and **Simulation** (no input) | 🟡 (simulation checkbox) |
| T7.F2 | Serial port field + Detect button + dropdown of available ports | 🟡 (field, auto-detect when blank) |
| T7.F3 | Debug: log every Arduino command | ❌ |
| T7.F4 | Test connection button | ✅ (PONG check) |
| T7.F5 | Capture card device index | skip (we don't use a capture card) |

### T8 — Recorder

| ID | Function | Today |
|---|---|---|
| T8.F1 | Output file + Browse, Load | 🟡 |
| T8.F2 | Status line (Not recording / Recording) + live position | 🟡 |
| T8.F3 | Start / Stop recording | ❌ |
| T8.F4 | Add-waypoint buttons with hotkeys: Walk F6, Rope F7, Shovel F8, Ladder F4, Label F9, Refill check F10 | 🟡 (type dropdown, no hotkeys) |
| T8.F5 | Stairs: Move down / Move up + direction (north/south/east/west) | ❌ |
| T8.F6 | Actions: Undo (F5) | ❌ |
| T8.F7 | Recorded waypoint table (#, type, coordinates) | ✅ |

### T9 — Status

| ID | Function | Today |
|---|---|---|
| T9.F1 | System resources: CPU % and RAM % bars, memory MB, thread count | ❌ |
| T9.F2 | Player stats: HP/MP bars; capacity, food, speed, stamina | 🟡 (HP/MP, capacity) |
| T9.F3 | Position: coordinates + Copy button, floor, "last update N s ago" | 🟡 (no copy, no age) |
| T9.F4 | Battle info: creatures in range | ✅ |

### T10 — Diagnostics

| ID | Function | Today |
|---|---|---|
| T10.F1 | Subsystem health: one dot + last result per reader (capture, vitals, battle, minimap, floor, …) | ❌ |
| T10.F2 | Connections: Arduino dot + status | ❌ |
| T10.F3 | Recovery status: stuck, reconnect, safe mode | ❌ |
| T10.F4 | Recent errors log | 🟡 (event log tail) |

Not taken from Tibiaeye: Telemetry (no telemetry server here), the capture card mode.

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
