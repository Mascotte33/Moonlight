# GUI remake

We are rebuilding Moonlight's GUI. It should **work** (every option drives the
bot) and **look like** [Tibiaeye](https://github.com/GGotha/tibiaeye)'s GUI.

## Start here (for agents)

1. Read **Rules** and **Testing** below. They apply to every task.
2. Do the next task in **Tasks** without a ✅ line, whose **Needs** all have one. They are listed in build order: Look (S) first, then the config work (T0), then the tabs.
3. Look up your tab in **Tabs** for what it should end up with.
4. Style it with **Look**. Use `moonlight/gui/theme.py`, never raw colors.
5. Touch only the task's **Files**. **Don't touch** lists what is off limits.
6. pytest, `ruff check` and `mypy moonlight tests` stay green.
7. **Do one task, then stop.** Don't commit; the owner checks and commits. When it is committed, add `- ✅ Done in commit <hash>.` under the task's heading. End with a report:
   - **Changed:** each file and what changed in it, one line each.
   - **What to expect:** what the owner will see when they open the GUI (`python -m moonlight.gui --no-bot`).
   - **Check it:** the commands you ran and their result, then the **Owner checks** from the task as a list to tick.
   - **Not done / unsure:** anything skipped or guessed.

**Stop and ask the owner** (don't work around it) when:
- the **Done when** check can't be met as written;
- the change needs a file outside the task's **Files**;
- a Tibiaeye screenshot doesn't show what a function should do.

> **Tibiaeye has no license.** Copy its look and features from the
> screenshots only. Never copy, paste or adapt its code.

---

## Rules

1. **One config.** Tabs read and write the real config: `BotConfig` in
   `moonlight/setup/config.py`, stored as a named file in `configs/` (repo root),
   e.g. `configs/rotworms.json`. Until T0.F1.K3 it is `moonlight_config.json`.
   The GUI's own copy (`gui/config_bridge.py`, saving to `data/config.json`)
   is deleted in T0.
2. **One tab pattern.** Each tab file starts with an `OPTIONS` list (shape in
   T0.F3.K1). Add an option = add one entry. Remove it = delete the entry.
   - Buttons and custom widgets go in `build()`.
   - Live updates go in `on_poll()`.
   - Writing to the config goes in `apply()`.
3. **Named configs.** Settings has a dropdown of the saved configs in `configs/`
   and **New**, **Save** and **Delete** buttons (T0.F1.K5).
   Apply writes the picked config at once. Start runs the picked config. The
   GUI remembers the last pick.
4. **Changes apply on Start.** The bot reads its config when it starts. To use
   new values, press Stop, then Start. Live reload comes later (T0.F2.K1).

**Why rule 1 matters:** today the tabs save to `data/config.json`, but Start
(`gui/loop_factory.py`) builds the bot from `moonlight_config.json`. Only
simulation mode, serial port, baud rate and profile name get through. Editing a
healing threshold in the GUI changes nothing in the running bot.

**Facts about the config agents need:**
- `BotConfig` can load (`BotConfig.load`, `from_dict`) but **cannot save yet**.
  T0.F1.K1 adds that.
- It is a **frozen** dataclass. Change a value with `dataclasses.replace`, never by assignment.
- Module sections (`healing`, `targeting`, `cavebot`, …) are `None` when the
  module is off. Writing an option into a `None` section creates it from its kept values in `BotConfig.off` (T0.F1.K4), or from defaults if there are none.
- `moonlight_config.json` may not exist. `load_real_config` in
  `gui/loop_factory.py` then returns `BotConfig.default()`.

**Already exists, reuse it:**
- `tabs/base.py`: the `Tab` base class (`build`, `on_poll`) and `section()`,
  which already builds a titled card. Restyle these; don't add a second card widget.
- `gui/state_bridge.py`: the bot state and loop control the tabs read from.

---

## Testing

There are no GUI tests yet. Put new ones in `tests/unit/` (e.g.
`tests/unit/test_gui_options.py`).

- **Test the logic, not the widgets.** No test opens a window. Test what can
  run headless: an option change produces the right `BotConfig`, saving writes
  the file, the top bar's text is formatted right.
- **Visual checks are the owner's.** "Done when" lists them as **Owner checks:**.
  Don't claim them as tested.

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
- Needs: T0.F3.K1
- Files: moonlight/gui/tabs/cavebot.py, tests/unit/test_gui_cavebot.py
- Don't touch: other tabs.
- Done when: `pytest tests/unit/test_gui_cavebot.py` passes (adding a waypoint stores the live x/y/z). Owner checks: the button is in the Cavebot tab.
```

---

## Tasks

#### Phase 1: Look

### S.F6.K1 — Theme file
- ✅ Done in commit 3204a08.
- What: add `moonlight/gui/theme.py` with the colors, fonts, spacing and radii from **Look**. Replace every raw color in `gui/` with a theme name. Restyle `section()` in `tabs/base.py` as the card.
- Files: moonlight/gui/theme.py, moonlight/gui/app.py, moonlight/gui/tabs/*.py
- Don't touch: layout and behavior; colors and fonts only.
- Done when: `grep -rn '"#' moonlight/gui` finds colors only in `theme.py`. Owner checks: every tab uses the Look colors.

### S.F1.K1 — Sidebar navigation
- ✅ Done in commit 7aacf8a.
- What: replace the top tab bar with a left sidebar (logo, one entry per tab, active entry highlighted).
- Needs: S.F6.K1
- Files: moonlight/gui/app.py
- Don't touch: the tab files.
- Done when: pytest stays green. Owner checks: every tab opens from the sidebar; the active one is highlighted.

### S.F1.K2 — Blue hover on sidebar entries
- What: a hovered, not-selected sidebar entry turns soft blue `NAV_HOVER` (`#2a3d5e`), weaker than the selected entry's `MANA`. It replaces the unused `NAV_ACTIVE`.
- Needs: S.F1.K1
- Files: moonlight/gui/theme.py, moonlight/gui/app.py (`select_tab` only), GUI_REMAKE.MD (Colors table)
- Don't touch: the tab files.
- Done when: pytest stays green. Owner checks: hovering a not-selected sidebar entry shows a soft blue; the selected entry stays the stronger blue.

### S.F2.K1 — Top bar
- ✅ Done in commit 3df15fc.
- What: one bar pinned to the top of the window, across its full width (above the sidebar too): HP bar + %, MP bar + %, state dot + text (Running / Paused / Stopped), and ▶ Start / ⏸ Pause / ■ Stop icon buttons. It belongs to the window, not to a tab, so it never changes or scrolls away. Put the text formatting in a plain function so it can be tested.
- Needs: S.F6.K1
- Files: moonlight/gui/app.py, moonlight/gui/state_bridge.py, tests/unit/test_gui_topbar.py
- Don't touch: the tab files (Settings keeps its buttons until its tab task).
- Done when: `pytest tests/unit/test_gui_topbar.py` passes (HP/MP % and state text from a `WorldState`). Owner checks: switching tabs leaves the bar in place; HP/MP update live; the buttons work from any tab.
- Deferred: "HP/MP update live", "the buttons work from any tab" and "Pause stops the bot" need a calibrated client. The owner tests them once calibration (CALIBRATION.MD) is in.

### S.F4.K1 — Bottom bar
- ✅ Done in commit 4ab171e.
- What: a strip at the bottom: Screen, Arduino and Bot dots; CPU % of the whole PC (0–100) and memory MB of the bot process. No new dependency (`psutil` is not installed): CPU % from `GetSystemTimes` deltas via `ctypes` on Windows (`—` elsewhere), memory from `GetProcessMemoryInfo` via `ctypes` on Windows, `—` elsewhere. Screen = `WorldState.screen_ok`; Arduino = green when `HardwareController.backend` is connected; Bot = `BotController.is_running`.
- Needs: S.F6.K1
- Files: moonlight/gui/app.py, tests/unit/test_gui_bottombar.py
- Don't touch: the tab files.
- Done when: `pytest tests/unit/test_gui_bottombar.py` passes (dot color from a status value). Owner checks: the Arduino dot turns green after Test connection succeeds; the Bot dot follows Start/Stop; CPU and MB update.

### S.F6.K2 — Disabled buttons
- ✅ Done in commit 286b910.
- What: a disabled button is `RAISED` with `TEXT_MUTED` text, whatever its enabled color. Today a disabled LIFE/AMBER/BLOOD button keeps its fill and its grey text is hard to read (the top bar's Pause and Stop).
- Needs: S.F2.K1
- Files: moonlight/gui/theme.py, moonlight/gui/app.py, tests/unit/test_gui_theme.py
- Don't touch: the tab files (their buttons get it in their tab tasks).
- Done when: `pytest tests/unit/test_gui_theme.py` passes. Owner checks: with `--no-bot`, the top bar's Pause and Stop are grey, and they get their color back when the bot runs.

#### Phase 2: Config (needed by every tab task)

### T0.F1.K1 — Config can save
- ✅ Done in commit a1345df.
- What: add `BotConfig.to_dict()` (the exact JSON `from_dict` reads; cavebot is saved as its route name) and `BotConfig.save(path)`.
- Files: moonlight/setup/config.py, tests/unit/test_config.py
- Don't touch: the GUI.
- Done when: `pytest tests/unit/test_config.py` passes, with a test that `BotConfig.from_dict(cfg.to_dict()) == cfg` for `BotConfig.default()` and for a config with every section set.

### T0.F1.K4 — Turned-off modules keep their values
- ✅ Done in commit 68972dd.
- What: a module that is off keeps its last values, so turning it back on restores them instead of the defaults. Add `BotConfig.off: dict[str, <module config>]`, which holds the values of each module that is off. `from_dict` reads a section like `{"enabled": false, "normal_hp_percent": 65}` into `off`, and `to_dict` writes it back the same way. The bot never reads `off`, so an off module stays `None` and stays off.
- Needs: T0.F1.K1
- Files: moonlight/setup/config.py, tests/unit/test_config.py
- Don't touch: the GUI, `bot/loop.py`, `bot/wiring.py`.
- Done when: `pytest tests/unit/test_config.py` passes, with a test that an off healing section with `normal_hp_percent: 65` round-trips, and that the bot still sees healing as off.

### T0.F1.K2 — Start uses only the real config
- ✅ Done in commit 477ba5c. The two Start owner checks wait until the calibration has `game_grid` and `status_icons`.
- What: Start builds the bot from `moonlight_config.json` alone. `BotConfig` gets `simulation` and `serial_baudrate`, so nothing Start needs comes from the GUI's copy. `gui/config_bridge.py` stays for now, because the tabs still use its GUI-only fields. The last tab task deletes it once every tab is converted.
- Needs: T0.F1.K1
- Files: moonlight/setup/config.py, moonlight/gui/loop_factory.py, moonlight/gui/app.py (`start_bot` only), tests/unit/test_loop_factory.py
- Don't touch: the tabs, `gui/config_bridge.py`.
- Done when: `pytest tests/unit/test_loop_factory.py` passes and `loop_factory.py` doesn't import `config_bridge`. Owner checks: the GUI still opens, and Start follows `"simulation"` in `moonlight_config.json`.

### T0.F3.K1 — Tab option pattern
- What: add the shared pattern from Rule 2 to `tabs/base.py`. One option is:
  `Option(label: str, field: str, control: "slider" | "switch" | "entry" | "dropdown", min: float | None = None, max: float | None = None, choices: list[str] | None = None)`,
  where `field` is a dotted path into `BotConfig`, e.g. `"healing.normal_hp_percent"`. `Tab` builds one control per entry and gets a default `apply()` that writes every option into a new `BotConfig` with `dataclasses.replace`, then saves it.
- Needs: T0.F1.K1
- Files: moonlight/gui/tabs/base.py, tests/unit/test_gui_options.py
- Don't touch: the tab files (each tab task converts its own tab).
- Done when: `pytest tests/unit/test_gui_options.py` passes, with tests that a dotted-path write gives the right `BotConfig`, a write into a `None` section creates it, and out-of-range values are refused.
- ✅ Done in commit f7ed7b1. A value written into a turned-off module keeps it off; only its `<module>.enabled` switch turns it on.

### T0.F1.K3 — Named configs: dropdown, Save as, Apply saves
- What: configs live in `configs/<name>.json`. Settings replaces its Save config / Load config buttons with:
  - a **dropdown** of the names in `configs/`. Picking one loads it into every tab;
  - a **Save as** button that asks for a name and saves the current values as `configs/<name>.json`, then picks it. A name that already exists asks before overwriting. Names are letters, digits, `-` and `_`;
  - Apply on each tab saves the picked config (the `apply()` from T0.F3.K1). Start builds the bot from the picked config (`load_real_config(path)` in `gui/loop_factory.py`).
  - The last pick is stored in `configs/last.txt` and picked again on the next start.
  - First run with an empty `configs/`: copy `moonlight_config.json` to `configs/default.json` if it exists, else save `BotConfig.default()` there.
- Needs: T0.F1.K2, T0.F3.K1
- Files: moonlight/gui/tabs/settings.py, moonlight/gui/state_bridge.py, moonlight/gui/loop_factory.py, moonlight/gui/tabs/base.py (`apply()` saves the picked config; controls refill on a pick), tests/unit/test_gui_configs.py
- Don't touch: `tools/check_setup.py` (it already takes `--config configs/<name>.json`).
- Done when: `pytest tests/unit/test_gui_configs.py` passes, with tests that Save as writes the named file, a bad name is refused, the dropdown lists the files, the last pick is restored, and the first run creates `configs/default.json`. Owner checks: Save as a new name, pick it in the dropdown, change a value, Apply, Stop then Start, and the bot uses the new value.
- ✅ Done in commit 6f584d9. The owner check waits until a tab uses options (the tab tasks) and calibration lets Start run.

### T0.F1.K5 — Config buttons: New, Save, Delete
- What: in Settings, next to the config dropdown:
  - **Save as** becomes **New**, with the same behaviour;
  - **Save** writes every tab's current option values into the picked config (each tab's `apply()`);
  - **Delete** asks, then deletes the picked config and picks the first one left. The only config left can't be deleted.
- Needs: T0.F1.K3
- Files: moonlight/gui/tabs/settings.py, moonlight/gui/state_bridge.py, moonlight/gui/loop_factory.py, moonlight/gui/tabs/base.py, tests/unit/test_gui_configs.py
- Don't touch: the other tab files.
- Done when: `pytest tests/unit/test_gui_configs.py` passes, with tests that Delete removes the file and picks another, the only config can't be deleted, and Save runs every tab's `apply()`. Owner checks: New makes a config; Delete asks, then removes it from the dropdown.

#### Phase 3: Tabs

### T2.F7.K1 — Start calibration button
- What: in Settings, a **Start calibration** button that opens the calibration window (`moonlight/setup/calibrate.py`) for the current profile, plus an editable **UI scale** number read from and saved to that profile's `ui_scale`. The rest of calibration is in `CALIBRATION.MD`.
- Needs: CALIBRATION.MD C2.F3.K1 and C3.F1.K1
- Files: moonlight/gui/tabs/settings.py, tests/unit/test_gui_options.py
- Don't touch: calibrate.py, autofind.py.
- Done when: `pytest tests/unit/test_gui_options.py` passes (UI scale writes the profile). Owner checks: the button opens the calibration window, and saving replaces the profile.

### T0.F2.K1 — Live reload (later)
- What: the running loop picks up config changes without Stop/Start.
- Needs: T0.F1.K3
- Files: moonlight/bot/loop.py, moonlight/bot/wiring.py, moonlight/gui/loop_factory.py
- Done when: do not build yet.

_Tab tasks (T1–T10) are not written yet. They get written here, in the format above, before Phase 3 starts. Each tab task also converts that tab to the T0.F3.K1 pattern._

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
| `NAV_ACTIVE` | `#1e2332` | unused (too faint for the selected entry; owner picked `MANA`) |
| `TEXT` | `#e7edf2` | values, headings |
| `TEXT_DIM` | `#b6b8bd` | labels |
| `TEXT_MUTED` | `#7c8186` | section titles, hints, disabled |
| `LIFE` | `#73c9a5` | HP bar, Start, "running", healthy dot |
| `MANA` | `#6ba4f8` | MP bar, links, focus, selected sidebar entry (`NIGHT` text) |
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
