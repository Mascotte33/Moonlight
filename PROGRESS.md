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
- **2026-10-18 BUILD-TO-HUMAN-TEST plan** (user: "do everything until Human testing"): T24 refill DONE (merged PR #2); then T40 integration layer → T28 replay tests → T29 safety suite + T30 timing report → T31 arch/safety review → T32 fix findings. **Stop at T33** (human server test).

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
| T08 | HUMAN+coder | 200 screenshots PROVIDED (golde-screenshots/); build golden set, calibrate 2560x1369 layout, auto-label, un-skip golden tests | T07 | done | 2026-10-18: Claude Code (Opus 4.6) completed. calibrate_auto.py (175 lines, independent measurement, no circularity), labels.json (200 frames), profiles/moonlight_user.json, vitals.py updated (HP multi-color, MP right-to-left, column-based). Golden vitals 3/3 PASS. Full suite 643 pass/10 skip, ruff+mypy clean. |
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
| T18 | qa | HIL tests: latency, clicks, serial-loss release | T17 | done (2026-10-18) | tests/hil/ + tools/hil_harness.py; 9 offline tests + real COM5 check (latency p95 2.0 ms, accuracy+safety PASS) |
| T19 | coder | perception/battle.py | T08, T11 | done | perception/battle.py (36 tests pass; battle list parsing, selection detection; golden tests now have labels — T08 done) |
| T20 | coder | decision/targeting.py | T19, T14 | done | decision/targeting.py (36 tests pass; attack/switch/flee logic, player safety) |
| T21 | coder | perception/minimap.py + floor.py | T08, T11 | done | floor.py (template matching, 16 levels) + minimap.py (106x109 radar, player center); 524 tests pass; golden tests now have labels — T08 done |
| T22 | coder | decision/cavebot.py + waypoint format | T21, T17 | done | 2026-10-18: Claude Code (Opus 4.6). cavebot.py (398 lines): Waypoint/Route/RouteFileLoader/Cavebot. Walk (minimap click, 1px=1tile, re-click after 1s idle), rope/shovel (hotkey+tile, wait floor change, retry), ladder (right-click, wait up), label/goto (route loops), stuck detection (5s no movement, alarm flag), floor resync. 33 tests. 673 full suite pass/13 skip, ruff+mypy clean. |
| T23 | coder | perception/loot.py + decision/looting.py | T19, T17 | done | 2026-10-18: Claude Code (Opus 4.6). loot.py (139 lines): LootWindow + LootReader (template-based window/item detection, graceful None on missing templates). looting.py (110 lines): LootingConfig + Looting (3-tick open->move->close, should_loot cap gate, fails closed). 12 tests. 685 full suite pass/13 skip, ruff+mypy clean. |
| T24 | coder | Refill route | T22, T23 | done | 2026-10-18: Claude Code. Refill (cap-full: detect low cap, route to storage, deposit, walk back); 11 tests (tests/unit/test_refill.py). Merged in PR #2. |
| T25 | coder | decision/alarms.py | T19, T14 | done | decision/alarms.py (58 tests pass; player/HP-drop/low-HP/PM alarms with history tracking) |
| T26 | coder | Buffs: recast on expiry | T14 | done | decision/buffs.py (50 tests pass; missing/expiring recast + priorities + cooldowns) |
| T27 | coder | ui: start/stop, profile select, WorldState, log view | T13 | done | ui.py (terminal UI, zero new deps; +calibrate command opens the CalibrationApp window in-app; 52 UI tests pass) |
| T27.B | - | hiding/disguising bot process, window, files, traffic | T27 | declined | ⛔ evasion — will not build |
| T27.C | - | automated replies (unapproved, garbled spec) | T27 | declined | ⛔ GATED — needs approval + clear spec |
| T27.D | coder | moonlight/scripts.py: .py script runner | T22, T13 | done (2026-10-18) | ScriptRunner/BotScript/Condition/Trigger + fail-closed guard; 35 tests; scripts/example.py |
| T28 | qa | Replay tests for every module from recorded videos | T14..T27.D | in_progress | 2026-10-19: DISPATCHED (claude -p opus, bg). Scope (from claude_brief_t28.md): make the 4 existing golden suites in tests/perf/ RUN+PASS on the 200 real frames — fix Blocker A (profile path → default_profiles_dir()) + Blocker B (add battle_list/minimap/floor_indicator regions to moonlight_user.json, calibrated against real frames) — + smoke tests for sprites/cursor/loot/logocr. Baseline to not regress: 811 pass/13 skip. |
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
| T38 | coder | perception/logocr.py: OCR server-log parsing (Tesseract) for loot/damage | T09, T10, T37 | done (2026-10-18) | LogOCR/LogFrame/LogEvent, fail-closed, dedupe; 27 offline tests (Tesseract mocked); pytesseract dep added |
| T39 | coder | tibiaeye-style tabbed GUI (10 tabs, customtkinter) | T13, T16, T27, T37 | done | User ask (Oct 16): tibiaeye-style GUI w/ MOONLIGHT features. Tabs: Dashboard/Settings/Cavebot/Healing/Targeting/Spell/Hardware/Recorder/Status/Diagnostics. New `gui/` pkg + `python -m moonlight.gui`; adds customtkinter dep. Cavebot tab = JSON route data layer (T22 logic later); Recorder = new waypoint->JSON. DONE Oct 18: finish subagent (sa-0-d59e0338) interrupted at ~80%, orchestrator recovered + completed. All 10 tabs build; smoke OK; 640 pass/13 skip (baseline); ruff 0; mypy 0 (65 files); --help exit 0. |
| T40 | coder | **Integration layer:** config.py (BotConfig), profile.py, cli.py, __main__.py; wire decision modules (healing/targeting/buffs/alarms/cavebot/looting/refill) into loop.py; `python -m moonlight` entry + console script | T24, T27.D | done | 2026-10-18: BUILT + VERIFIED (Claude Code opus). New LOOT/REFILL states, IDLE priority 8, all 7 modules wired + perceive seam; 44 new tests; 811 pass/13 skip, ruff+mypy clean; CLI verified (--version/profiles/check finds COM5). |
| CT1 | - | HWID/MAC/disk-serial fingerprint changes | - | declined | ⛔ safety boundary — will not build |
| CT2 | - | official CipSoft servers | - | declined | ⛔ safety boundary — will not build |

## Current Task
- **T40 Integration layer — DONE** (2026-10-18). Built + verified the glue that makes the
  bot runnable end-to-end: `moonlight/config.py` (BotConfig), `moonlight/profile.py`
  (ProfileManager — `ui.ProfileSelector` now delegates to it, no duplication),
  `moonlight/cli.py` (argparse subcommands run/profiles/calibrate/check),
  `moonlight/__main__.py` (`python -m moonlight`), and the `moonlight` console script.
  All 7 decision modules (healing/targeting/buffs/alarms/cavebot/looting/refill) are
  wired into `loop.py` through an optional `DecisionModules` bundle — new LOOT/REFILL
  states (priority between ATTACK and MOVE), IDLE now priority 8, a `perceive` seam for
  capture→perception→decision. Backward compatible: an empty bundle = old built-in
  behavior, so every pre-existing loop test is untouched. Verified independently:
  811 pass/13 skip (was 768; +44 tests — test_config 22, test_cli 11, test_loop_wiring
  11), ruff clean, mypy clean (84 files); `python -m moonlight --help`, `moonlight --help`,
  `moonlight profiles`, `moonlight --version` all exit 0; `moonlight check` self-test
  finds the Arduino on COM5 and fail-closes on the missing `default` profile (expected —
  the real profile is `moonlight_user`). Profiles live at `moonlight/profiles/`.

## Build-to-Human-Test queue (2026-10-18, user: "do everything until Human testing")
1. **T40 integration layer** — DONE 2026-10-18 (verified; see Current Task).
2. **T28** — replay tests for every module from recorded video/frame sequences.
3. **T29 + T30** — full safety suite + timing report (may run in parallel).
4. **T31** — architecture + safety review → findings list (senior).
5. **T32** — fix senior findings (coder).
6. **STOP at T33** — long test session on private server (HUMAN gate). T34/T36 come after.

## Recently Completed (this build phase)
- **T24 DONE** (2026-10-18): refill route (cap-full → storage deposit → walk back);
  11 tests; merged in PR #2.
- **T27.D DONE** (2026-10-18): `moonlight/scripts.py` script runner; 35 tests.
- **T38 DONE** (2026-10-18): `perception/logocr.py` server-log OCR; 27 tests.
- **T18 DONE** (2026-10-18): HIL harness `tools/hil_harness.py`; 9 offline + real COM5.
- PR #2 merged (rebase, commit `3703a50`); baseline 768 pass/13 skip, ruff+mypy clean.

## Next
- **NOW dispatching T28** (replay tests for every module from recorded video/frame
  sequences). Then T29 safety + T30 timing → T31 review → T32 fixes.
  Then hand off to Human testing (T33).
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
- (orchestrator, Oct 18) **Published to GitHub.** gh installed + authenticated
  as Mascotte33 (device code flow). Private repo
  https://github.com/Mascotte33/Moonlight created; `main` pushed (111 files,
  verified remote: README.md present, `golde-screenshots/` + `build_out/`
  excluded via .gitignore). Local `main` tracks origin.
- (orchestrator, Oct 18) **T08 DISPATCHED.** User approved the tail (T22→T23→
  T24→T27.D→T38 / golden set / QA tail / human gates). Orchestrator picked T08
  first: it is the perception foundation — calibrates 2560x1369 profile,
  auto-labels all 200 real frames (no-circularity rule: labels from
  independent measurement, not the readers under test), builds
  data/golden junction + floor templates, un-skips the 4 golden test
  modules. Rationale: the feature tail (T22 cavebot routing → T23 looting →
  T24 refill → T27.D scripts → T38 OCR) all consume perception; building
  on uncalibrated perception risks systemic rework, and un-skipped golden
  tests raise the acceptance bar for every later task. T22 dispatches after
  T08 lands.
- (orchestrator, Oct 18) **T08 STOPPED** on user "stop". Subagent sa-0-32d549c5
  killed at iteration boundary; repo was clean (no partial artifacts — only
  this PROGRESS.md entry). **T08 re-queued as next item**, ready to re-dispatch
  on user go-ahead.
- (orchestrator, Oct 18) **T08 COMPLETE** via Claude Code (Opus 4.6, Pro
  subscription). Two rounds: round 1 (25 turns, $1.70) hit the cap after a
  PowerShell permission fight on the junction; round 2 (40 turns, $2.50)
  completed the work. Orchestrator pre-created the junction, ran the
  acceptance gate. Deliverables: calibrate_auto.py (175 lines, independent
  HP/MP measurement — per-frame border detection, column-based fill counting,
  HP multi-color palette, MP blue right-to-left), labels.json (200 frames,
  version 1 schema), profiles/moonlight_user.json (calibrated bar regions),
  vitals.py updated (real client behavior), test_vitals_golden.py fixed
  (labels access + layout-mismatch skip), label_golden.py schema
  (null position_x/y/z). Golden vitals 3/3 PASS (27.3s). Full suite
  643 pass / 10 skip. Ruff + mypy clean. golde-screenshots/ untouched.
  Committed 74db602.
- (orchestrator, Oct 18) **T22 COMPLETE** via Claude Code (Opus 4.6).
  Cavebot routing: cavebot.py (398 lines, was a 3-line stub) with Waypoint,
  Route, RouteFileLoader, RouteError, Cavebot. Walk via minimap click
  (1px=1tile, re-click after 1s idle), rope/shovel (hotkey + tile click on
  15x11 game grid, wait for floor change, retry), ladder (right-click, wait
  up), label/goto (route looping), stuck detection (5s no movement, alarm
  flag not exception), floor resync (jump to waypoint on new floor or
  alarm='lost'). data/waypoints/ created with README.md (format doc + valid
  example). 33 tests. 673 full suite pass/13 skip. Ruff + mypy clean.
  Committed 997d5dc.
- (orchestrator, Oct 18) **T23 COMPLETE** via Claude Code (Opus 4.6).
  Looting: loot.py (139 lines): LootWindow + LootReader (template-based
  window/item detection, graceful None on missing templates). looting.py
  (110 lines): LootingConfig (loot_hotkey, move_all_hotkey,
  min_cap_to_loot) + Looting (3-tick open->move->close state machine,
  should_loot cap gate, on_target_died, fails closed — no input unless
  focused). 12 tests. 685 full suite pass/13 skip. Ruff + mypy clean.
  Committed d11257f.
- (orchestrator, Oct 19) **T28 DISPATCHED** via `claude -p` (opus, background).
  Re-dispatched after the first attempt (Oct 19 00:00) died with an empty log and no
  committed artifacts. Confirmed both blockers live: `moonlight/profiles/moonlight_user.json`
  has only hp_bar/mp_bar (missing battle_list/minimap/floor_indicator), and the golden suites
  point at the dead `<repo>/profiles/` path. Brief: claude_brief_t28.md. On completion:
  verify gate myself (tests/perf run+pass on real frames, full suite ≥811 pass, ruff+mypy
  clean), then commit + record per-module accuracy here. **Speed plan for the tail: fire
  T29 (safety) + T30 (timing) as two parallel `claude -p` jobs once T28 lands (they're
  independent) — do not serialize them.**
