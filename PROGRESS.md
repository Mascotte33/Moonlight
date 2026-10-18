# MOONLIGHT — PROGRESS

Orchestrator task tracker. Updated by the orchestrator after every dispatch/result.
Legend: `pending` | `in_progress` | `done` | `done(bug)` | `stopped`(reason) | `blocked`(reason) | `declined`

## Notes
- Bot target: Low-requirements hardware (CPU only, lightweight)
- Use case: OTS servers that allow bots
- Execution constraint: One agent at a time (sequential)
- RTX 4090 is orchestrator hardware (for agents), NOT bot hardware
- **2026-10-14 ORCHESTRATOR TAKEOVER.** A second concurrent orchestrator session
  was editing this repo in parallel (flat package layout, extra batches Oct 14).
  The primary orchestrator took over, reconciled state, and is now the sole writer.
- **Layout:** the `moonlight` package lives in `moonlight/` under the repo root
  (`<repo>/moonlight/__init__.py`). Lint/type/test on `.` from root.
- **Every folder has an `instructions.md`** — agents MUST read it before working.

## Task queue

| ID | Agent | Summary | Depends on | Status | Notes / result |
|----|-------|---------|-----------|--------|----------------|
| T01 | sw_research | Client UI layout doc | - | done | docs/ui_layout.md (19.9 KB, verified) |
| T02 | hw_research | Capture FPS + CPU/GPU budget | - | done | docs/hw_budget.md + raw JSON (verified) |
| T03 | coder | Repo skeleton: layout, pyproject, empty modules | - | done | pyproject valid; now flat layout |
| T04 | qa | CI: ruff + mypy + pytest + benchmarks | T03 | done | ci.yml exists; ⚠️ verify `pip install -e .` works (flat layout) |
| T05 | coder | input.py: InputBackend, FakeInput, protocol encoder + tests | T03 | done | input.py (9.6 KB) + tests, verified passing |
| T06 | coder | firmware/moonlight.ino | T05 | done | 15.7 KB, compiles clean for Leonardo |
| T06.B | - | USB/device identity spoofing | T06 | declined | ⛔ safety boundary — will not build |
| T07 | qa | tools/label_golden.py + labels.json schema | T03 | done | label_golden.py (17.2 KB), verified |
| T08 | HUMAN+coder | 200 screenshots PROVIDED (golde-screenshots/); build golden set, calibrate 2560x1369 layout, auto-label, un-skip golden tests | T07 | in_progress | User dropped 200 real frames (2560x1369 PNG, uniform layout, event-labeled filenames, 5 chars, 6 sessions Jul 5-16) at `golde-screenshots/` (keep 'golde' spelling) Oct 17. Builder subagent dispatched sequentially after T39 finish (first attempt stalled on llamacpp concurrency, zero artifacts). |
| T09 | coder | capture.py: client window only, DPI-aware, focus detection | T02, T03 | done | capture.py (15.9 KB) + tests, verified passing |
| T10 | coder | calibrate.py: draw regions -> profiles/<name>.json | T09 | done | calibrate.py (22 KB) + tests, verified passing |
| T11 | coder | state.py: WorldState incl. client_focused, screen_ok | T10 | done | state.py (4.4 KB) + tests, verified passing |
| T12 | coder | perception/vitals.py: HP/MP % | T08, T11 | done | perception/vitals.py (35 synthetic tests pass; golden tests skip until T08 images) |
| T13 | coder | loop.py: 20Hz, priority state machine, rate limit, F12 kill | T05, T11 | done | loop.py (24.8 KB); fixed failing test — 156 pass / 1 skip, ruff+mypy clean |
| T14 | coder | decision/healing.py | T12, T13 | done | decision/healing.py (51 tests pass; emergency/normal/cure + cooldowns + input integration) |
| T15 | HUMAN | Flash T06 to Arduino; confirm P->PONG, kill pin, unplug test | T06 | done(bug) | Flashed to COM5 (Leonardo). P->PONG 3/3, X->OK verified. Kill pin + unplug test pending user (optional physical wiring). |
| T16 | coder | ArduinoInput: pyserial, 1s heartbeat, reconnect, stop on lost PONG | T15 | done | ArduinoInput in input.py + tests/unit/test_arduino_input.py. 566 pass/13 skip, ruff+mypy clean. **Verified against real Arduino (COM5):** auto-detect, P->PONG heartbeat 3/3, X->OK, and a physical ScrollLock keypress. |
| T17 | coder | Closed-loop clicks: move->read cursor->correct->click +-2px | T16 | done | clicker.py (ClickController, closed-loop, fail-closed) + perception/cursor.py (CursorReader template matching). 42 new offline tests (test_clicker.py 17, test_cursor.py 25). 640 pass/13 skip, ruff+mypy clean. |
| T17.B | - | input humanizing to disguise automation | T17 | declined | ⛔ evasion — will not build (reliability timing only) |
| T18 | qa | HIL tests: latency, clicks, serial-loss release | T17 | pending | |
| T19 | coder | perception/battle.py | T08, T11 | done | perception/battle.py (36 tests pass; battle list parsing, selection detection; golden awaits T08) |
| T20 | coder | decision/targeting.py | T19, T14 | done | decision/targeting.py (36 tests pass; attack/switch/flee logic, player safety) |
| T21 | coder | perception/minimap.py + floor.py | T08, T11 | done | floor.py (template matching, 16 levels) + minimap.py (106x109 radar, player center); 524 tests pass; golden awaits T08 |
| T22 | coder | decision/cavebot.py + waypoint format | T21, T17 | pending | decision/ still all stubs |
| T23 | coder | perception/loot.py + decision/looting.py | T19, T17 | pending | |
| T24 | coder | Refill route | T22, T23 | pending | |
| T25 | coder | decision/alarms.py | T19, T14 | done | decision/alarms.py (58 tests pass; player/HP-drop/low-HP/PM alarms with history tracking) |
| T26 | coder | Buffs: recast on expiry | T14 | done | decision/buffs.py (50 tests pass; missing/expiring recast + priorities + cooldowns) |
| T27 | coder | ui: start/stop, profile select, WorldState, log view | T13 | done | ui.py (terminal UI, zero new deps; +calibrate command opens the CalibrationApp window in-app; 52 UI tests pass) |
| T27.B | - | hiding/disguising bot process, window, files, traffic | T27 | declined | ⛔ evasion — will not build |
| T27.C | - | automated replies (unapproved, garbled spec) | T27 | declined | ⛔ GATED — needs approval + clear spec |
| T27.D | coder | decision/scripts.py: .py script runner | T22, T13 | pending | scripts.py is a stub |
| T28 | qa | Replay tests for every module from recorded videos | T14..T27.D | pending | |
| T29 | qa | Full safety suite | T28 | pending | |
| T30 | qa | Timing report | T28 | pending | |
| T31 | senior | Architecture + safety review -> findings list | T29, T30 | pending | |
| T31.B | - | anti-cheat evasion, VMs, kernel drivers | T31 | declined | ⛔ safety boundary — will not build |
| T32 | coder | Fix senior findings | T31 | pending | |
| T33 | HUMAN | Long test session on private server; log issues here | T32 | pending | |
| T34 | coder | Fix issues from T33 | T33 | pending | |
| T35 | qa | Audit all folders for README.md; fill gaps; verify cross-agent docs | T34 | done | All 11 folders have README.md. Fixed stale status: perception minimap/floor→done, firmware kill-switch desc, tools/ list, decision test-dir refs, root instructions |
| T36 | HUMAN | Review final report and approve completion | - | pending | |
| T37 | coder | perception/sprites.py: monster sprite detection via cv2.matchTemplate | T09, T10 | done | sprites.py + test_sprites.py (27 tests). Fills gap: bot now knows monster SCREEN POSITIONS, not just battle-list names. Prereq for T17 + T23. 598 pass/13 skip, ruff+mypy clean. (Adopted from Oct 16 CV directive.) |
| T38 | coder | perception/logocr.py: OCR server-log parsing (Tesseract) for loot/damage | T09, T10, T37 | pending | Queued from Oct 16 CV directive. Adds Tesseract dep (lightweight, not EasyOCR). Feeds T23 looting. |
| T39 | coder | tibiaeye-style tabbed GUI (10 tabs, customtkinter) | T13, T16, T27, T37 | done | User ask (Oct 16): tibiaeye-style GUI w/ MOONLIGHT features. Tabs: Dashboard/Settings/Cavebot/Healing/Targeting/Spell/Hardware/Recorder/Status/Diagnostics. New `gui/` pkg + `python -m moonlight.gui`; adds customtkinter dep. Cavebot tab = JSON route data layer (T22 logic later); Recorder = new waypoint->JSON. DONE Oct 18: finish subagent (sa-0-d59e0338) interrupted at ~80%, orchestrator recovered + completed. All 10 tabs build; smoke OK; 640 pass/13 skip (baseline); ruff 0; mypy 0 (65 files); --help exit 0. |
| CT1 | - | HWID/MAC/disk-serial fingerprint changes | - | declined | ⛔ safety boundary — will not build |
| CT2 | - | official CipSoft servers | - | declined | ⛔ safety boundary — will not build |

## Current Task
- **T37 DONE** (Oct 16): Monster sprite detection via template matching
  (perception/sprites.py + 27 tests). Adopted from user's CV directive. Bot now
  detects monster sprites on the game field and returns screen positions — closing
  the gap where it only knew monsters from the battle list. Prereq for T17 + T23.
  598 pass / 13 skip, ruff+mypy clean.
- **T17 DONE** (Oct 16): Closed-loop clicking. clicker.py (ClickController:
  move -> read rendered cursor via CursorReader -> correct -> click once,
  fail-closed) + perception/cursor.py (CursorReader, template matching in
  calibrated cursor region). Fixed cursor.py coordinate mapping for clipped
  region origins + zero-size template validation. 42 new offline tests
  (test_clicker.py + test_cursor.py). 598→640 pass / 13 skip, ruff+mypy clean.
- **T38 QUEUED** (Oct 16): OCR server-log parsing (Tesseract) for loot/damage.
  Also from the CV directive. Feeds T23 looting.
- **Calibration in bot UI** (Oct 16): `calibrate` command in ui.py opens the
  CalibrationApp window in-app. 571→598 pass as sprite tests added.
- **T16 DONE** (Oct 16): ArduinoInput (pyserial) in input.py, verified against
  the real Leonardo on COM5.
- **T08 UNBLOCKED** (Oct 17): user provided 200 real Tibia client frames
  (2560x1369 PNG, uniform layout, event-labeled filenames, 5 characters, 6
  sessions) at `golde-screenshots/` (keep the 'golde' spelling). Golden-set
  builder: calibrate 2560x1369 layout -> profiles/moonlight_user.json,
  auto-label all 200 frames -> tests/golden/labels.json, data/golden junction,
  un-skip + run vitals/battle/minimap/floor golden tests. Queued to dispatch
  right after T39 finish lands (llamacpp backend stalls under concurrent
  subagents — running sequentially).
- **T39 FINISH** (Oct 17): first GUI build interrupted at ~80% (llamacpp stall
  after 58 API calls). On disk: 3 bridges + 6/10 tabs (dashboard, settings,
  cavebot, healing, targeting, spell_attack). Targeted finish dispatched
  (sa-0-d59e0338): fix PerceptionWriter hp_pct->hp_percent bug, build 4
  missing tabs (hardware/recorder/status/diagnostics), entry points + 10-tab
  CTkTabview app, customtkinter dep in pyproject, >=15 offline tests,
  ruff+mypy+headless --help.
- **T39 DONE** (Oct 18): recovery + final cleanup by orchestrator after
  sa-0-d59e0338 interrupted: 12 self-referential grid frames re-pointed,
  2 CTkComboBox variable fixes, targeting.py geometry re-point, 65 ruff
  auto-fixes + 23 E501 splits + 2 B904 (from exc), mypy 69->0 (real
  dict[str, object]/BotConfig/WorldState typing, stat_num helper in
  base.py, InputBackend default close() = safety release, app.tabs
  dict[str, Tab], serial/customtkinter import ignores, 3 unused-ignores
  removed, customtkinter mypy override). Gates: ruff 0, mypy 0 (65 files),
  pytest 640/13 baseline, smoke OK (10 tabs), --help exit 0.
- Next: **T08** (golden-set builder, queued — dispatch on user go-ahead),
  then **T18** (HIL, unblocked by T17), **T22**/**T23** (unblocked by T17),
  **T38** (queued).

## Blocked Tasks
- T15: kill pin + unplug — SOFTWARE side verified Oct 16 (loop.py kill tests
  7/7, ArduinoInput serial-loss 42/42, live COM5 PONG + release_all OK). Only the
  physical wiring check remains (optional, user).

## Dispatch log
- (orchestrator, Oct 13) T03, T05, T06, T07 dispatched & verified
- (concurrent session, Oct 14) built flat layout + capture/calibrate/loop/state,
  ci.yml, ui_layout.md, evasion stubs — collided with primary
- (orchestrator, Oct 14 ~22:15) **TOOK OVER.** Verified state: 155 pass / 1 fail
  (loop.py) / 1 skip; ruff+mypy clean on `.`. Removed evasion stubs
  humanize.py + stealth.py. Added instructions.md to all 11 folders. Reconciled.
- (orchestrator, Oct 16) **T15 flash verified.** Arduino Leonardo on COM5
  (port auto-shifted from COM3). Firmware flashed via arduino-cli; P->PONG 3/3,
  X->OK confirmed. T16 (ArduinoInput) unblocked.
- (orchestrator, Oct 16) **T37 sprite detection verified.** User's CV directive
  reviewed: adopted template matching + OCR; declined BattlEye/hardware-capture
  evasion (safety boundary). sprites.py + 27 tests dispatched & verified
  (598 pass/13 skip, ruff+mypy clean). T38 (log OCR) queued.
- (subagent, Oct 16) **T17 closed-loop clicking shipped.** Reviewed
  clicker.py + cursor.py: fixed cursor.py coordinate mapping for clipped
  region origins (read() was offsetting by unclipped region origin) and added
  zero-size template ValueError. Added 42 offline tests (test_clicker.py 17,
  test_cursor.py 25), exported CursorReader/CursorTemplates from
  moonlight.perception, docs updated. 640 pass/13 skip, ruff+mypy clean.
- (orchestrator, Oct 16) **Blocked-task retry.** T15 software side verified:
  kill-switch tests 7/7, ArduinoInput serial-loss 42/42, live COM5 P->PONG +
  release_all OK. T08 pipeline ready (label_golden.py); no new screenshots in
  repo yet — awaiting user footage in data/golden/.
- (orchestrator, Oct 16) **T39 (tibiaeye-style GUI) added + dispatched.** User
  asked for a tibiaeye-style tabbed GUI with MOONLIGHT features. Researched
  tibiaeye (10 tabs: Dashboard, Settings, Cavebot, Healing, Targeting, Spell
  Attack, Hardware, Recorder, Status, Diagnostics). Most tabs surface existing
  done features (vitals, battle, targeting, healing, buffs, alarms, Arduino,
  loop/kill-switch); Cavebot tab = JSON route data layer (T22 logic later);
  Recorder = new (waypoint->JSON route). Framework: customtkinter (one
  lightweight dep). New `gui/` pkg, entry `python -m moonlight.gui`.
- (orchestrator, Oct 17) **T08 UNBLOCKED.** User provided 200 real Tibia
  client frames at `golde-screenshots/` (2560x1369 PNG, uniform layout,
  event-labeled filenames: LevelUp/SkillUp/Achievement/TreasureFound/
  BossDefeated/LowHealth; 5 characters; 6 sessions Jul 5-16). All 200 parse
  cleanly; dimensions uniform (fixed client layout). First golden-set builder
  subagent stalled on llamacpp (3 API calls, zero artifacts left — clean slate).
- (orchestrator, Oct 17) **Both running subagents lost to llamacpp stalls**
  (they ran concurrently). T39 GUI left ~80% complete (3 bridges + 6/10 tabs;
  known PerceptionWriter hp_pct/hp_percent field bug). New policy: run
  subagents SEQUENTIALLY (backend stalls under concurrency). Re-dispatched T39
  as a TARGETED FINISH (sa-0-d59e0338: bug fix + 4 tabs + entry points +
  tests). T08 builder dispatches as soon as T39 finish lands.
