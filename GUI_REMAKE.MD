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
- ✅ Done in commit 82dd06d.
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

### S.F6.K3 — One soft blue hover for dark controls
- ✅ Done in commit 1afbb3b.
- What: `HOVER` becomes the soft blue `#2a3d5e`, so every dark button and dropdown hovers the same blue as the sidebar. `NAV_HOVER` goes away; the sidebar uses `HOVER`. Colored buttons keep their own `*_HOVER`.
- Needs: S.F1.K2
- Files: moonlight/gui/theme.py, moonlight/gui/app.py (`select_tab` only), GUI_REMAKE.MD (Colors table)
- Don't touch: the tab files.
- Done when: pytest stays green and nothing uses `NAV_HOVER`. Owner checks: New, Save and the config dropdown hover the same soft blue as the sidebar.

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
- ✅ Done in commit 06ece84.

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

Tab tasks are in build order. Each one converts its part of the tab to the T0.F3.K1 pattern and removes that part's use of `gui/config_bridge.py`. Functions the bot can't do yet are listed under **Not planned yet** at the end.

### T7.F1.K1 — Hardware options
- ✅ Done in commit a3cb90a.
- What: convert Hardware to `OPTIONS`:
  - `simulation`: switch "Simulation", with a one-line hint under it ("No Arduino. Actions are only logged.");
  - `serial_port`: entry. Blank means auto-detect and is saved as `null`;
  - `serial_baudrate`: entry.
  Remove the tab's `config_store` save. Test connection stays as it is.
- Needs: T0.F1.K3
- Files: moonlight/gui/tabs/hardware.py, moonlight/gui/tabs/base.py (a blank entry writes `None` into a `str | None` field), tests/unit/test_gui_options.py
- Don't touch: other tabs, `HardwareController` in state_bridge.py.
- Done when: `pytest tests/unit/test_gui_options.py` passes, with a test that a blank port writes `serial_port=None`. Owner checks: change the baud rate, Save, reopen the GUI, and the value is still there.

### T7.F2.K1 — Detect ports
- ✅ Done in commit 9fe5c7b.
- What: a **Detect** button next to the port entry fills a dropdown with `ctx.hardware.list_ports()`. Picking a port puts it in the entry. No ports shows "No ports found".
- Needs: T7.F1.K1
- Files: moonlight/gui/tabs/hardware.py
- Don't touch: other tabs.
- Done when: pytest stays green. Owner checks: with the Arduino plugged in, Detect lists its port.

### T7.F3.K1 — Log every Arduino command
- ✅ Done in commit 4218e1e.
- What: add `BotConfig.log_commands: bool = False` and a switch for it in Hardware. When it is on, Start wraps the input backend so every command it sends goes to the event log (`ctx.log`) as one line.
- Needs: T7.F1.K1
- Files: moonlight/setup/config.py, moonlight/gui/loop_factory.py (`make_backend`), moonlight/gui/tabs/hardware.py, tests/unit/test_loop_factory.py
- Don't touch: moonlight/bot/loop.py.
- Done when: `pytest tests/unit/test_loop_factory.py` passes, with a test that a sent command shows up in the log when the switch is on and not when it is off. Owner checks: switch on, Start in simulation, and Dashboard's Recent events shows the commands.

### T4.F1.K1 — Healing options
- ✅ Done in commit 1fd07ee.
- What: convert Healing to `OPTIONS`:
  - `healing.enabled`: switch;
  - `healing.emergency_hp_percent`, `healing.normal_hp_percent`, `healing.mana_threshold`: sliders 0–100;
  - `healing.heal_cooldown_ms`: slider 0–2000;
  - `healing.emergency_spell`, `healing.normal_spell`, `healing.cure_spell`: entries.
  Next to each spell, a hotkey entry that writes `hotkeys[<spell>]`. `hotkeys` is a dict, so this goes in `build()` and `apply()`, not in `OPTIONS`. A spell without a hotkey is refused (`BotConfig` already checks this), and the error shows in the tab.
- Needs: T0.F1.K3
- Files: moonlight/gui/tabs/healing.py, tests/unit/test_gui_healing.py
- Don't touch: other tabs, moonlight/decision/healing.py.
- Done when: `pytest tests/unit/test_gui_healing.py` passes, with tests that Apply writes the thresholds and each spell's hotkey, and that a spell with no hotkey is refused. Owner checks: set Normal HP to 65, Save, reopen the GUI, and it shows 65. "The bot heals at 65" waits for calibration.

### T2.F2.K1 — Settings options
- ✅ Done in commit 9b25533.
- What: convert Settings to `OPTIONS`, starting with `loop.tick_rate` (slider 1–60). Remove what now lives elsewhere: Start/Pause/Kill and the status text (top bar, S.F2.K1), serial port and baud rate (Hardware, T7.F1.K1), input mode (Hardware's Simulation switch). The Config section (dropdown, New, Save, Delete) stays at the top.
- Needs: T7.F1.K1
- Files: moonlight/gui/tabs/settings.py, tests/unit/test_gui_options.py
- Don't touch: other tabs.
- Done when: `pytest tests/unit/test_gui_options.py` passes, with a test that the tick rate option writes `loop.tick_rate`. Owner checks: Settings has no Start/Pause/Kill, port or baud; the tick rate is kept after Save and reopening the GUI.

### T2.F4.K1 — Module switches
- ✅ Done in commit c6929e1.
- What: a **Modules** section in Settings with one `<module>.enabled` switch per bot module: healing, targeting, cavebot, looting, refill, buffs, alarms. Cavebot needs a route, so turning it on before a route is picked (T3.F1.K1) shows the `ConfigError` text and the switch goes back off.
- Needs: T2.F2.K1
- Files: moonlight/gui/tabs/settings.py, tests/unit/test_gui_options.py
- Don't touch: Diagnostics (its duplicate switches go in T10.F1.K1).
- Done when: `pytest tests/unit/test_gui_options.py` passes, with tests that switching looting off and on keeps its values, and that cavebot with no route is refused. Owner checks: turn Looting off, Save, reopen, and it is still off.

### T2.F3.K1 — Window picker
- ✅ Done in commit d6541a2.
- What: add `BotConfig.window_title: str` (default: the capture's `TIBIA_WINDOW_TITLE`). Start passes it to `ScreenCapture` (`loop_factory.py`). Settings gets a dropdown of the titles of open windows plus **Refresh**. The titles come from `EnumWindows` via `ctypes` on Windows, and the list is empty elsewhere. No new dependency.
- Needs: T2.F2.K1
- Files: moonlight/setup/config.py, moonlight/gui/loop_factory.py, moonlight/gui/tabs/settings.py, tests/unit/test_config.py, tests/unit/test_loop_factory.py
- Don't touch: moonlight/perception/capture.py (it already takes `window_title`).
- Done when: `pytest tests/unit/test_config.py tests/unit/test_loop_factory.py` passes, with tests that `window_title` round-trips and that Start passes it to the capture. Owner checks: with the game open, its window is in the dropdown.

### T5.F1.K1 — Targeting options
- ✅ Done in commit 8521608.
- What: convert Targeting to `OPTIONS`:
  - `targeting.enabled`, `targeting.prefer_low_hp`, `targeting.player_protection`: switches;
  - `targeting.max_monsters_to_engage`: slider 1–10;
  - `loop.attack_combo`: entry.
  The whitelist and blacklist stay GUI-only until T5.F1.K2.
- Needs: T0.F1.K3
- Files: moonlight/gui/tabs/targeting.py, tests/unit/test_gui_options.py
- Don't touch: other tabs, moonlight/decision/targeting.py.
- Done when: `pytest tests/unit/test_gui_options.py` passes, with a test that max monsters writes `targeting.max_monsters_to_engage`. Owner checks: values are kept after Save and reopening the GUI.

### T5.F1.K2 — Whitelist or blacklist in the bot
- ✅ Done in commit f6242d5.
- What: `TargetingConfig` gets `mode: "all" | "whitelist" | "blacklist"` (default `"all"`) and `creatures: tuple[str, ...]`. `TargetingDecision` attacks only listed creatures in whitelist mode, and skips listed creatures in blacklist mode. Names match without case. The tab gets a mode dropdown and one creature list editor (name entry, Add, Remove), replacing the two GUI-only lists.
- Needs: T5.F1.K1
- Files: moonlight/decision/targeting.py, moonlight/setup/config.py (only if `from_dict` needs it for the tuple), moonlight/gui/tabs/targeting.py, tests/unit/test_targeting.py
- Don't touch: other decision modules.
- Done when: `pytest tests/unit/test_targeting.py` passes, with tests for each mode, including that `"all"` behaves as today. Owner checks: add a creature, Save, reopen, and it is still in the list.

### T6.F1.K1 — Attack spells in the bot and the tab
- ✅ Done in commit 9a910de.
- What: today no bot module casts attack spells. Add one:
  - `moonlight/decision/attack_spells.py` with `AttackSpellsConfig`: `single_spell`, `aoe_spell`, `aoe_min_monsters` (default 3), `mana_reserve_percent` (default 20), `cooldown_ms` (default 2000);
  - each tick with a selected target and MP above the reserve, it casts the AoE spell when at least `aoe_min_monsters` monsters are on screen, else the single spell. Blank spell = not used. The keys come from `hotkeys`, like healing;
  - add `attack_spells` to `MODULES`, `BotConfig` and `wiring.py`;
  - convert the Spell attack tab to `OPTIONS` for these fields, plus `attack_spells.enabled`, and hotkey entries next to the spells (as in T4.F1.K1).
- Needs: T5.F1.K1, T4.F1.K1
- Files: moonlight/decision/attack_spells.py, moonlight/setup/config.py, moonlight/bot/wiring.py, moonlight/gui/tabs/spell_attack.py, tests/unit/test_attack_spells.py
- Don't touch: healing and targeting decisions.
- Done when: `pytest tests/unit/test_attack_spells.py` passes, with tests for AoE vs single, the mana reserve, the cooldown, and no target = no cast. Owner checks: values are kept after Save and reopening. "The bot casts" waits for calibration.

### T1.F1.K1 — Dashboard stat cards
- ✅ Done in commit 3312350.
- What: four stat cards: Uptime (from the loop's `uptime_seconds`, shown as `h:mm:ss`), Ticks, Creatures on screen, HP %. Values use the hero size from **Look**. Remove the module list read from the GUI config (Settings has the switches). The text formatting is a plain function.
- Needs: T0.F1.K3
- Files: moonlight/gui/tabs/dashboard.py, tests/unit/test_gui_dashboard.py
- Don't touch: other tabs.
- Done when: `pytest tests/unit/test_gui_dashboard.py` passes (uptime text, `—` when the bot is stopped). Owner checks: Start in simulation and the uptime counts up.

### T1.F2.K1 — Bot health dots
- ✅ Done in commit b0d32c1.
- What: a **Bot health** card with one dot per subsystem the bot can report today: Screen (`screen_ok`), Vitals (`last_update` under 2 s old), Minimap (position not `(0, 0, 0)`), Arduino (backend connected), Bot (running). Put the check in one plain function in `tabs/base.py` that returns `(name, ok)` pairs. The bottom bar and Diagnostics use the same function. A battle list dot waits until perception reports per-reader results.
- Needs: T1.F1.K1
- Files: moonlight/gui/tabs/base.py, moonlight/gui/tabs/dashboard.py, moonlight/gui/app.py (the bottom bar uses the function), tests/unit/test_gui_dashboard.py
- Don't touch: other tabs.
- Done when: `pytest tests/unit/test_gui_dashboard.py tests/unit/test_gui_bottombar.py` passes, with a test of each dot from a `WorldState`. Owner checks: with `--no-bot` every dot is red; the Arduino dot turns green after Test connection.

### T1.F3.K1 — Current task
- ✅ Done in commit 40e619c.
- What: the loop reports what it did last tick in `stats()["current_task"]`: `Idle`, `Healing`, `Attacking`, `Looting` or `Walking to waypoint N`. The Dashboard shows it under the stat cards.
- Needs: T1.F1.K1
- Files: moonlight/bot/loop.py, moonlight/gui/tabs/dashboard.py, tests/unit/test_loop.py
- Don't touch: the decision modules.
- Done when: `pytest tests/unit/test_loop.py` passes, with a test per task text. Owner checks: in simulation it shows Idle. The other texts wait for calibration.

### T9.F1.K1 — System card
- ✅ Done in commit 70959ac.
- What: CPU % and RAM % bars for the whole PC, the bot's memory MB, and its thread count (`threading.active_count()`). Move the bottom bar's CPU and memory readers from `app.py` into `moonlight/gui/sysinfo.py` and use them in both places. RAM % comes from `GlobalMemoryStatusEx` via `ctypes` on Windows, `—` elsewhere.
- Needs: S.F4.K1
- Files: moonlight/gui/sysinfo.py, moonlight/gui/app.py, moonlight/gui/tabs/status.py, tests/unit/test_gui_bottombar.py
- Don't touch: other tabs.
- Done when: `pytest tests/unit/test_gui_bottombar.py` passes. Owner checks: the Status tab's CPU matches the bottom bar's.

### T9.F3.K1 — Position card
- What: position `x, y, z` in Cascadia Mono, a **Copy** button that puts `x, y, z` on the clipboard, and "updated N s ago" from `last_update`. The age text is a plain function.
- Needs: T9.F1.K1
- Files: moonlight/gui/tabs/status.py, tests/unit/test_gui_status.py
- Don't touch: other tabs.
- Done when: `pytest tests/unit/test_gui_status.py` passes (age text, "never" when `last_update` is 0). Owner checks: Copy, then paste somewhere, and it shows the position.

### T10.F1.K1 — Subsystem health and Arduino
- What: a **Health** card with the T1.F2.K1 dots and each one's last value (e.g. "HP 82%", "pos 1024, 980, 7", "COM3 connected"). This covers T10.F1 and T10.F2. Remove Diagnostics' module switches (Settings owns them, T2.F4.K1).
- Needs: T1.F2.K1, T2.F4.K1
- Files: moonlight/gui/tabs/diagnostics.py
- Don't touch: other tabs.
- Done when: pytest stays green and diagnostics.py doesn't use `config_store`. Owner checks: the dots match the Dashboard's.

### T10.F4.K1 — Recent errors
- What: a **Recent errors** card with the last 20 log lines at warning or error level, newest first, and "No errors" when there are none. `LogFeed` already keeps the level.
- Needs: T10.F1.K1
- Files: moonlight/gui/tabs/diagnostics.py, moonlight/gui/state_bridge.py (a level filter on `LogFeed.text` if needed), tests/unit/test_gui_status.py
- Don't touch: other tabs.
- Done when: `pytest tests/unit/test_gui_status.py` passes, with a test that info lines are left out. Owner checks: a failed Test connection shows in the card.

### T3.F1.K1 — Route picker on the bot's routes
- What: the GUI uses the bot's route format and folder (`data/waypoints/`, `BotConfig.routes_dir`, read by `RouteFileLoader`). This settles the route folder open decision. Cavebot gets:
  - a dropdown `Option` on `cavebot.route` with the route names in `routes_dir` (`to_dict` saves the route by name, so the value round-trips), plus **Reload**;
  - `cavebot.rope_hotkey` and `cavebot.shovel_hotkey` entries;
  - a waypoint table (#, action, x, y, z, label) of the picked route, and "No waypoints loaded" when it is empty.
  Remove the tab's `config_store` use and its `gui/routes.py` use. Loop route is now a `goto` waypoint in the route, so the Loop route switch goes away.
- Needs: T0.F1.K3
- Files: moonlight/gui/tabs/cavebot.py, tests/unit/test_gui_cavebot.py
- Don't touch: moonlight/decision/cavebot.py, the Recorder tab.
- Done when: `pytest tests/unit/test_gui_cavebot.py` passes, with tests that picking a route writes `cavebot.route` and the table rows come from the file. Owner checks: a route in `data/waypoints/` shows in the dropdown and its waypoints show in the table.

### T3.F4.K1 — Current waypoint
- What: in the waypoint table the current waypoint (`Route.current`, read from the running cavebot) is marked `>`, and a line under it says "Waypoint N of M". Stopped bot = no marker, "Not running".
- Needs: T3.F1.K1
- Files: moonlight/gui/tabs/cavebot.py, moonlight/gui/state_bridge.py (read the cavebot's route from the running loop), tests/unit/test_gui_cavebot.py
- Don't touch: moonlight/decision/cavebot.py.
- Done when: `pytest tests/unit/test_gui_cavebot.py` passes (the line text from index and length). Owner checks: wait for calibration.

### T3.F2.K1 — Start from waypoint N
- What: a number entry "Start from waypoint" (1 to the route's length). Start sets the route's `current` to N − 1 before the loop runs. It is saved as `cavebot.start_index` in `CavebotConfig` (default 0).
- Needs: T3.F1.K1
- Files: moonlight/setup/config.py, moonlight/bot/wiring.py, moonlight/gui/tabs/cavebot.py, tests/unit/test_loop_wiring.py
- Don't touch: moonlight/decision/cavebot.py.
- Done when: `pytest tests/unit/test_loop_wiring.py` passes, with a test that the built cavebot starts at the given index, and that an index past the end is refused. Owner checks: wait for calibration.

### T3.F6.K1 — Add current position as a waypoint
- What: a button that appends the player's live position as a `walk` waypoint to the picked route file and refreshes the table. Position `(0, 0, 0)` (unknown) shows "Position unknown" and adds nothing.
- Needs: T3.F1.K1
- Files: moonlight/gui/tabs/cavebot.py, tests/unit/test_gui_cavebot.py
- Don't touch: other tabs.
- Done when: `pytest tests/unit/test_gui_cavebot.py` passes (adding a waypoint stores the live x/y/z; unknown position adds nothing). Owner checks: the button is in the Cavebot tab.

### T3.F7.K1 — Refill sub-tab
- What: a **Refill** sub-tab in Cavebot with `OPTIONS` for what the bot has: `refill.enabled`, `refill.trigger_cap` and `refill.stop_cap` (entries, oz), `refill.route_name` (dropdown of routes), `refill.storage_hotkey`, `refill.deposit_hotkey` (blank = no deposit). Remove the GUI-only refill HP %, MP % and deposit fields. Potion counts wait (see Not planned yet).
- Needs: T3.F1.K1
- Files: moonlight/gui/tabs/cavebot.py, tests/unit/test_gui_cavebot.py
- Don't touch: moonlight/decision/refill.py.
- Done when: `pytest tests/unit/test_gui_cavebot.py` passes, with a test that a blank deposit hotkey writes `None`. Owner checks: values are kept after Save and reopening.

### T3.F8.K1 — Loot sub-tab
- What: a **Loot** sub-tab in Cavebot with `OPTIONS` for `looting.enabled`, `looting.loot_hotkey`, `looting.move_all_hotkey` (blank = none), `looting.max_items_per_loot` (slider 1–50), `looting.min_cap_to_loot` (entry, oz).
- Needs: T3.F1.K1
- Files: moonlight/gui/tabs/cavebot.py, tests/unit/test_gui_cavebot.py
- Don't touch: moonlight/decision/looting.py.
- Done when: `pytest tests/unit/test_gui_cavebot.py` passes. Owner checks: values are kept after Save and reopening.

### T8.F1.K1 — Recorder saves bot routes
- What: the Recorder saves and loads routes in the bot's format in `routes_dir` (x, y, z, action, label), so the Cavebot can run them. Output file = a route name entry, plus a dropdown of existing routes to load. Waypoint types become the bot's actions: walk, rope, shovel, ladder, label, goto.
- Needs: T3.F1.K1
- Files: moonlight/gui/tabs/recorder.py, tests/unit/test_gui_recorder.py
- Don't touch: moonlight/decision/cavebot.py, the Cavebot tab.
- Done when: `pytest tests/unit/test_gui_recorder.py` passes, with a test that a saved route loads with `RouteFileLoader`. Owner checks: record two waypoints, save, and the route shows in the Cavebot dropdown.

### T8.F3.K1 — Start and stop recording
- What: **Start recording** / **Stop recording** buttons and a status line (Not recording / Recording, plus the live position). While recording, a `walk` waypoint is added when the player has moved at least N tiles from the last one (entry, default 5) or changed floor.
- Needs: T8.F1.K1
- Files: moonlight/gui/tabs/recorder.py, tests/unit/test_gui_recorder.py
- Don't touch: other tabs.
- Done when: `pytest tests/unit/test_gui_recorder.py` passes, with tests that a short step adds nothing and a long step or floor change adds a waypoint. Owner checks: wait for calibration.

### T8.F4.K1 — Recorder hotkeys
- What: hotkeys that work while the game has focus: F6 walk, F7 rope, F8 shovel, F4 ladder, F9 label, F5 undo. They use `RegisterHotKey` via `ctypes` on Windows (no new dependency), only while recording, and are released on Stop recording and on close. This covers T8.F4 and T8.F6.
- Needs: T8.F3.K1
- Files: moonlight/gui/tabs/recorder.py, tests/unit/test_gui_recorder.py
- Don't touch: other tabs.
- Done when: `pytest tests/unit/test_gui_recorder.py` passes, with a test that each key maps to its action. Owner checks: while recording in the game, F7 adds a rope waypoint and F5 removes it.

### T0.F1.K6 — Delete the GUI's config copy
- What: delete `moonlight/gui/config_bridge.py`, `moonlight/gui/routes.py`, `ctx.config_store` and their tests. Every tab now reads and writes `BotConfig`.
- Needs: every tab task above.
- Files: moonlight/gui/config_bridge.py, moonlight/gui/routes.py, moonlight/gui/state_bridge.py, moonlight/gui/app.py, their tests
- Don't touch: moonlight/setup/config.py.
- Done when: pytest stays green and `grep -rn "config_bridge\|gui.routes\|config_store" moonlight tests` finds nothing. Owner checks: every tab opens.

#### Not planned yet

These need bot features that don't exist. They get tasks when the bot can do them:
- T2.F4: Stuck alert, session logging, screen and debug overlay switches.
- T2.F5 server save and T2.F6 auto-reconnect.
- T3.F7: refill on potion counts (the bot can't count potions yet).
- T5.F3: attack the nearest creature (perception doesn't give creature positions).
- T8.F4: Refill check F10 and T8.F5 stairs (the bot has no such waypoint actions).
- T9.F2: food, speed, stamina (perception doesn't read them).
- T10.F3: recovery (stuck, reconnect, safe mode).

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
| `HOVER` | `#2a3d5e` | hovered dark buttons, dropdowns and sidebar entries (soft blue; the selected entry is `MANA`) |
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

Today: the cavebot engine works (`decision/cavebot.py`), but the tab uses the GUI's own route format in `data/routes/`.

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
- **Route folder:** settled. The GUI uses the bot's format in `data/waypoints/` (T3.F1.K1, T8.F1.K1).
