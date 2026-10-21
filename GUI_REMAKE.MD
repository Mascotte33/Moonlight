# GUI remake

We are rebuilding Moonlight's GUI. It should **work** (every option drives the
bot) and **look like** [Tibiaeye](https://github.com/GGotha/tibiaeye)'s GUI.

## Start here (for agents)

1. Read **Rules** below. They apply to every task.
2. Pick a task from **Tasks**. Do T0 first; everything builds on it.
3. Look up your tab in **Tabs** for what it should end up with.
4. Style it with **Look**. Use `moonlight/gui/theme.py`, never raw colors.
5. One task = one commit. pytest, `ruff check` and `mypy moonlight tests` stay green.

> **Tibiaeye has no license.** Copy its look and features from the
> screenshots only. Never copy, paste or adapt its code.

---

## Rules

1. **One config.** Tabs read and write the real config: `moonlight_config.json`,
   loaded by `moonlight/setup/config.py`. The GUI's own copy
   (`gui/config_bridge.py` and `data/config.json`) is deleted in T0.
2. **One tab pattern.** Each tab file starts with an `OPTIONS` list, one entry
   per option (label, config field, control type).
   - Add an option = add one entry. Remove it = delete the entry.
   - Buttons and custom widgets go in `build()`.
   - Live updates go in `on_poll()`.
   - Writing to the config goes in `apply()`.
3. **Apply saves.** Apply writes `moonlight_config.json` at once. Settings has
   no Save/Load config.
4. **Changes apply on Start.** The bot reads its config when it starts. To use
   new values, press Stop, then Start. Live reload comes later (T0.F2.K1).

**Why rule 1 matters:** today the tabs save to `data/config.json`, but Start
(`gui/loop_factory.py`) builds the bot from `moonlight_config.json`. Only
simulation mode, serial port, baud rate and profile name get through. Editing a
healing threshold in the GUI changes nothing in the running bot.

---

## IDs and task format

| Level | Format | Example |
|---|---|---|
| Tab | `T<n>` (`S` = the app shell around all tabs) | `T4` Healing |
| Functionality | `T<n>.F<m>` | `T4.F1` HP thresholds |
| Task | `T<n>.F<m>.K<k>` | `T4.F1.K2` wire thresholds into the loop |

A task is one change that can be built, tested and committed on its own.
Write every task as one block, so an agent can do it with no other context:

```
### T3.F6.K1 — Add current waypoint
- What: a button that appends the player's current minimap position to the route.
- Files: moonlight/gui/tabs/cavebot.py, tests/gui/test_cavebot_tab.py
- Done when: clicking it adds a waypoint with the live x/y/z; a test covers it.
```

---

## Tasks

### T0.F1.K1 — One config
- What: every tab reads/writes the real `BotConfig` (`moonlight/setup/config.py`). Delete `gui/config_bridge.py`'s copy and `data/config.json`.
- Files: moonlight/gui/config_bridge.py, moonlight/gui/loop_factory.py, moonlight/gui/tabs/*.py, tests/gui/
- Done when: change a Healing threshold in the GUI, press Start, and the bot uses the new value; a test proves it.

### T0.F1.K2 — Apply saves, remove Save/Load config
- What: each tab's Apply writes `moonlight_config.json`. Remove Save config / Load config from Settings.
- Files: moonlight/gui/tabs/settings.py, moonlight/gui/tabs/*.py, tests/gui/
- Done when: Apply on any tab changes the file on disk; Settings has no Save/Load buttons.

### S.F6.K1 — Theme file
- What: add `moonlight/gui/theme.py` with the colors, fonts, spacing and radii from **Look**. Replace every raw color in `gui/` with a theme name.
- Files: moonlight/gui/theme.py, moonlight/gui/app.py, moonlight/gui/tabs/*.py
- Done when: `grep -rn '"#' moonlight/gui` finds colors only in `theme.py`.

### S.F1.K1 — Sidebar navigation
- What: replace the top tab bar with a left sidebar (logo, one entry per tab, active entry highlighted).
- Files: moonlight/gui/app.py, tests/gui/
- Done when: every tab opens from the sidebar; the active one is highlighted.

### S.F2.K1 — Top bar
- What: one bar pinned to the top of the window, across its full width (above the sidebar too): HP bar + %, MP bar + %, state dot + text (Running / Paused / Stopped), and ▶ Start / ⏸ Pause / ■ Stop icon buttons. It belongs to the window, not to a tab, so it never changes or scrolls away.
- Files: moonlight/gui/app.py, moonlight/gui/state_bridge.py, tests/gui/
- Done when: switching between any tabs leaves the bar in place; HP/MP and state update live; the buttons work from any tab.

### S.F4.K1 — Bottom bar
- What: a strip at the bottom: Screen, Arduino and Bot dots; CPU % and memory MB of the bot process.
- Files: moonlight/gui/app.py, tests/gui/
- Done when: unplugging the Arduino turns its dot red within a second.

### T0.F2.K1 — Live reload (later)
- What: the running loop picks up config changes without Stop/Start.
- Files: moonlight/bot/loop.py, moonlight/bot/wiring.py, moonlight/gui/loop_factory.py
- Done when: changing a Healing threshold while the bot runs takes effect within one tick; a test proves it.

### T2.F7.K1 — Start calibration button
- What: in Settings, a **Start calibration** button that opens the calibration window (`moonlight/setup/calibrate.py`) for the current profile, plus an editable **UI scale** number read from and saved to that profile's `ui_scale`. The rest of calibration is in `CALIBRATION.MD`.
- Files: moonlight/gui/tabs/settings.py, tests/gui/
- Done when: the button opens the calibration window and saving it replaces the profile; changing UI scale and pressing Apply writes it to the profile.

_Tab tasks: the owner says what each tab needs; they get written up here in the format above._

---

## Look

Target: Tibiaeye's look, rebuilt in our code. Colors below were measured from
its screenshots.

**Who and what.** A player runs the bot on a second screen during a hunt and
glances at it now and then. In one second they must see three things: is it
running, am I safe (HP/MP), is anything broken. So the GUI should feel like a
**night-time instrument panel**: dark, quiet, dense, and color only where it
means something.

**Focal point.** The top bar: HP, MP, Running/Paused/Stopped and the ▶ ⏸ ■
buttons. It spans the whole window above the sidebar and the tab, and stays put
whatever tab is open. Tab content comes second.

```
┌──────────────────────────────────────────────────────────┐
│ HP ████████░░ 82%   MP ████░░░░ 47%   ● Running   ▶ ⏸ ■ │  top bar (always)
├────────────┬─────────────────────────────────────────────┤
│ sidebar    │ current tab                                 │
├────────────┴─────────────────────────────────────────────┤
│ ● Screen  ● Arduino  ● Bot              CPU 7% · 184 MB  │  bottom bar (always)
└──────────────────────────────────────────────────────────┘
```

**Signature.** Status dots. One small dot per subsystem (screen, vitals, battle
list, minimap, Arduino), green or red. The same dots appear in the bottom bar,
the Dashboard's Bot health card and Diagnostics, so "is anything broken" reads
the same everywhere.

**Defaults we reject.**
- CustomTkinter's default blue theme → neutral grays; color only for vitals, state and danger.
- Top tab bar → left sidebar.
- A grid of identical stat cards → state and vitals lead; cards are second-tier.

### Colors (`theme.py`)

| Name | Hex | Use |
|---|---|---|
| `NIGHT` | `#0d1117` | window and sidebar background |
| `PANEL` | `#171b22` | cards |
| `RAISED` | `#22262c` | inputs, card borders, hovered rows |
| `NAV_ACTIVE` | `#1e2332` | selected sidebar entry |
| `TEXT` | `#e7edf2` | values, headings |
| `TEXT_DIM` | `#b6b8bd` | labels |
| `TEXT_MUTED` | `#7c8186` | section titles, hints, disabled |
| `LIFE` | `#73c9a5` | HP bar, Start, "running", healthy dot |
| `MANA` | `#6ba4f8` | MP bar, links, focus |
| `AMBER` | `#c99b3e` | Pause, "paused", warnings |
| `BLOOD` | `#e55d51` | Stop, errors, failed dot |

Rules: about 90% of the screen is `NIGHT`/`PANEL`/`RAISED`. Never use `LIFE`,
`MANA`, `AMBER` or `BLOOD` for decoration. Inputs use `RAISED`, one step
lighter than their card.

### Type

- Font: Segoe UI. Numbers that change (HP, ticks, coordinates, CPU): Cascadia Mono, so they don't jump.
- Sizes: 11 section title (UPPERCASE, `TEXT_MUTED`) · 13 body and labels · 16 card heading · 26 hero numbers.
- Hierarchy comes from weight and color first, size second: values bold `TEXT`, labels normal `TEXT_DIM`.

### Spacing and shape

- Base unit 4 px. Padding inside a card: 12. Gap between cards: 12. Gap between sections: 24.
- Radius: 6 for buttons and inputs, 10 for cards.
- Depth: color steps plus a 1 px `RAISED` border on cards. No shadows (Tk can't do them well on dark).
- Sidebar: 200 px wide, below the top bar, same `NIGHT` as the window, one `RAISED` line on its right edge.
- Every control has hover, pressed and disabled states; every list has an empty state ("No waypoints loaded").

---

## Tabs

Legend: ✅ works · 🟡 exists but incomplete or not wired to the bot · ❌ missing

### S — App shell (around every tab)

| ID | Function | Today |
|---|---|---|
| S.F1 | Left sidebar navigation | ❌ top tab bar |
| S.F2 | Top bar, full window width, always visible: HP + %, MP + %, state dot + text | ❌ |
| S.F3 | Top bar: ▶ Start / ⏸ Pause / ■ Stop icon buttons | ❌ only in Settings |
| S.F4 | Bottom bar: Screen, Arduino, Bot dots | ❌ |
| S.F5 | Bottom bar: CPU % and memory MB | ❌ |
| S.F6 | Theme (see Look) | 🟡 dark, no cards, raw colors |

### T1 — Dashboard

Today: read-only and overlaps with Status.

| ID | Function | Today |
|---|---|---|
| T1.F1 | Stat cards: uptime, ticks, creatures, HP % | 🟡 no uptime |
| T1.F2 | Bot health: one dot per perception reader | ❌ |
| T1.F3 | Current task (Idle / Healing / Attacking / Walking to waypoint N) | ❌ |
| T1.F4 | Battle list | ✅ |
| T1.F5 | Recent events | ✅ |

### T2 — Settings

Today: everything except Start/Pause/Kill writes the GUI config the bot ignores.

| ID | Function | Today |
|---|---|---|
| T2.F1 | Start / Pause / Stop + status text | ✅ |
| T2.F2 | Tick rate (seconds) | 🟡 not read by the bot |
| T2.F3 | Window picker (dropdown of open windows) + Refresh | 🟡 text field |
| T2.F4 | Module toggles: Healing, Cavebot, Loot (+ hotkey), Stuck alert (+ timeout), Session logging, Screen overlay, Debug overlay | 🟡 only Healing, Loot, Cavebot, Overlay; not read by the bot |
| T2.F5 | Server save: enable + time (CET) | ❌ |
| T2.F6 | Auto-reconnect (contents not visible in the screenshot) | ❌ |
| T2.F7 | Start calibration button + UI scale number (see `CALIBRATION.MD`) | ❌ |

### T3 — Cavebot

Today: the cavebot engine is a placeholder (T22 pending).

| ID | Function | Today |
|---|---|---|
| T3.F1 | Route file + Browse, Reload, Load | 🟡 dropdown of saved routes |
| T3.F2 | Start from waypoint N | ❌ |
| T3.F3 | Loop route | ✅ |
| T3.F4 | Waypoint table (#, type, coordinates), current one marked `>` | 🟡 no current marker |
| T3.F5 | "Waypoint N of M" line | ❌ |
| T3.F6 | Add current position as a waypoint (owner request) | ❌ |
| T3.F7 | Refill sub-tab (below) | 🟡 HP%/MP% and deposit only |
| T3.F8 | Loot sub-tab (contents not visible in the screenshot) | ❌ |

Refill sub-tab (T3.F7):
- City.
- Refill when: HP potions < N, MP potions < N, capacity < N oz (toggle).
- Refill to: HP potion count + type, MP potion count + type.
- Deposit gold before refill.
- Return to label.

### T4 — Healing

Today: the GUI has the thresholds, the bot doesn't read them.

| ID | Function | Today |
|---|---|---|
| T4.F1 | HP/MP thresholds for potions and spells | 🟡 not read by the bot |

No Tibiaeye screenshot for this tab; the function comes from its README.

### T5 — Targeting

Today: max monsters, prefer lowest HP, player protection, attack hotkey. None reach the bot.

| ID | Function | Today |
|---|---|---|
| T5.F1 | Whitelist **or** blacklist mode | 🟡 both lists, no mode switch |
| T5.F2 | Creature list editor | 🟡 GUI only |
| T5.F3 | Attack the nearest creature in range | 🟡 bot picks first in battle list, or lowest HP |

### T6 — Spell attack

Today: no decision module uses these settings.

| ID | Function | Today |
|---|---|---|
| T6.F1 | AoE and single-target spell groups | 🟡 |
| T6.F2 | Mana reserve | 🟡 |

### T7 — Hardware

Today: works and feeds Start.

| ID | Function | Today |
|---|---|---|
| T7.F1 | Mode radio with a one-line description: **Arduino + mss**, **Simulation** | 🟡 simulation checkbox |
| T7.F2 | Serial port + Detect + dropdown of ports | 🟡 field; blank = auto-detect |
| T7.F3 | Debug: log every Arduino command | ❌ |
| T7.F4 | Test connection | ✅ PONG check |

### T8 — Recorder

Today: saves routes, but the cavebot can't use them yet.

| ID | Function | Today |
|---|---|---|
| T8.F1 | Output file + Browse, Load | 🟡 |
| T8.F2 | Status (Not recording / Recording) + live position | 🟡 |
| T8.F3 | Start / Stop recording | ❌ |
| T8.F4 | Add waypoint: Walk F6, Rope F7, Shovel F8, Ladder F4, Label F9, Refill check F10 | 🟡 type dropdown, no hotkeys |
| T8.F5 | Stairs: Move down / Move up + direction | ❌ |
| T8.F6 | Undo (F5) | ❌ |
| T8.F7 | Recorded waypoint table | ✅ |

### T9 — Status

| ID | Function | Today |
|---|---|---|
| T9.F1 | CPU % and RAM % bars, memory MB, threads | ❌ |
| T9.F2 | HP/MP bars; capacity, food, speed, stamina | 🟡 HP/MP and capacity |
| T9.F3 | Position + Copy, floor, "updated N s ago" | 🟡 no copy, no age |
| T9.F4 | Creatures in range | ✅ |

### T10 — Diagnostics

Today: tick rate, tick-time percentiles, capture latency, state freshness, Reset stats. Its module toggles duplicate T2.F4.

| ID | Function | Today |
|---|---|---|
| T10.F1 | Subsystem health: dot + last result per reader | ❌ |
| T10.F2 | Arduino connection dot + status | ❌ |
| T10.F3 | Recovery: stuck, reconnect, safe mode | ❌ |
| T10.F4 | Recent errors | 🟡 event log tail |

### Not in any tab yet

- Check setup button (card `task_21b3ca41`). Calibrate is now T2.F7.
- Buffs and Alarms (decision modules exist, no GUI).
- Looting settings.

Left out on purpose: Tibiaeye's Telemetry (no server here) and its Software / Capture card modes.

---

## Open decisions

- **Tab set** (merges, new Buffs/Alarms/Looting tabs): left as-is for now.
- **Route folder:** the GUI saves to `data/routes/`, but `decision/cavebot.py` and `decision/refill.py` read `data/waypoints/`. Settle it in the Cavebot tasks.
