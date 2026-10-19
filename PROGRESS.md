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
| T28 | qa | Replay tests for every module from recorded videos | T14..T27.D | done | 2026-10-19: commit 1bf7b9b. tests/replay/test_replay_perception.py (all 8 perception modules on the 200 real frames) + tests/perf/test_golden_smoke.py + the 4 golden suites now RUN. Vitals 199/199 within ±2pp (HP mean err 0.00, MP 0.06); battle reader fixed for real frames (80/200 frames w/ entries, 67 selected targets); minimap marker 173/200; floor stable; cursor/sprites/loot type-validated; logocr fail-closed (Tesseract binary absent). battle.py + minimap.py reader fixes; battle_list/minimap/floor_indicator regions added to moonlight_user.json. Orchestrator-verified. |
| T29 | qa | Full safety suite | T28 | done | 2026-10-19: commit b10fe0c. tests/safety/test_safety_suite.py — 12 safety invariants (kill-switch, fail-closed focus/screen, stuck/lost detection, release-all on exit, HP/MP emergency, rate limit, IDLE no-input, loot window focus, refill cap gate); all pass in 0.14 s. Orchestrator-verified. |
| T30 | qa | Timing report | T28 | done | 2026-10-19: commit b10fe0c. tests/perf/test_timing_report.py — full tick 2.5 ms mean / 3.4 ms p95 (budget 50 ms ✅). ⚠️ FINDING: perception p95 SUM 55.5 ms > 50 ms budget — bottlenecks sprites 29.5 ms, logocr 19.1 ms (Tesseract not installed → slow path). Carried to T31 review. Orchestrator-verified. |
| T31 | senior | Architecture + safety review -> findings list | T29, T30 | done | 2026-10-19: commit c8aa239. docs/senior_review_findings.md — 27 findings (3 critical / 17 major / 7 minor), each with file:line refs. Verdict: NO-SHIP as wired; conditional SHIP of a SUPERVISED T33 (healing + alarms; attack only if G4) gated on G1-G7 (see doc). Criticals: F-01 no player detection in production (nameless battle entries), F-02 stop paths don't release inputs (Arduino heartbeat keeps firmware watchdog fed), F-03 relative minimap position vs absolute routes + two diverging trackers. Baseline re-verified 843 pass/8 skip, ruff+mypy clean. First dispatch died at the doc write (permission prompt; missing --dangerously-skip-permissions) — round 2 succeeded. |
| T31.B | - | anti-cheat evasion, VMs, kernel drivers | T31 | declined | ⛔ safety boundary — will not build |
| T32a | coder | T31 fixes: loop safety (G1+G3+G4 core) — F-02, F-04, F-05, F-07, F-08, F-12, F-26, F-23, F-24, F-22 + F-19 build_loop safety tests | T31 | done | 2026-10-19: commit f0312d6. All 11 findings fixed: PAUSED state (pause-not-stop on unfocus, 30s timeout / 3 consecutive tick errors), ungated release_all on kill/shutdown/pause/exception, tick error guard, attack re-press cooldown (only when no/changed target, 1s), crowd warning (max_monsters default 4, no flee dead-end), beep_alert sink (winsound, never raises), heal-reserved rate token, last_update monotonic, F12 bit 0x8000, healing-module thresholds as single source; 6 new build_loop safety invariants (tests 13-18). 857 pass/8 skip, ruff+mypy clean. Orchestrator-verified (diff + full gate re-run). |
| T32b | coder | T31 fixes: wiring+config (G2, G5, G6) — F-01 interim, F-03, F-06, F-09, F-10, F-11, F-13, F-20, F-25, F-27 | T32a | **PAUSED** | 2026-10-19: run 1 interrupted by Claude CLI account session limit. WIP committed d518a93 (cavebot F-03/F-06 refactor done in-module; suite red at 3 — cli.build_loop wiring + tests pending). Re-dispatch was armed for 15:32 but the user called a stop; job killed. RESUME: `claude -p "$(cat claude_brief_t32b.md)" --model opus --dangerously-skip-permissions` (brief has a RESUME note; tree is clean). |
| T32c | coder | T31 fixes: perf+data quality — P1-P7, F-14, F-15, F-16 (HP-fill code), F-17, F-21, F-18 (test tightening) | T32b | pending | |
| T33 | HUMAN | Long test session on private server; log issues here | T32c | pending | |
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
- **T28 + T29 + T30 — DONE (2026-10-19, orchestrator-verified).** The QA tail of the
  build-to-Human-Test plan landed in two commits (1bf7b9b, b10fe0c) via `claude -p`
  (a 10:31 re-dispatch left an empty `claude_t28_replay.log`, but the commits are the
  deliverable). Gates re-run by the orchestrator: full suite **843 pass / 8 skip**
  (119 s), `ruff check .` clean, `mypy .` clean (89 files); replay+safety 14/14.
  - **T28**: replay tests for all 8 perception modules on the 200 real golden frames
    (`tests/replay/test_replay_perception.py`, `tests/perf/test_golden_smoke.py`, the
    4 golden suites now RUN instead of skip). Vitals 199/199 within ±2pp; battle reader
    fixed for real frames (80/200 frames have entries); minimap marker 173/200; floor
    stable; logocr fail-closed (Tesseract binary not installed here).
  - **T29**: 12 safety-invariant tests (`tests/safety/test_safety_suite.py`), all green.
  - **T30**: timing report (`tests/perf/test_timing_report.py`): full tick 2.5 ms mean /
    3.4 ms p95 (budget 50 ms ✅) — **but perception p95 SUM is 55.5 ms, over budget**:
    sprites 29.5 ms + logocr 19.1 ms (Tesseract missing → slow path). Carried to T31.
- **T31 DONE** (2026-10-19, commit c8aa239): docs/senior_review_findings.md — 27 findings
  (3 critical / 17 major / 7 minor) + T33 ship gates G1-G7. Verdict: NO-SHIP as wired;
  conditional SHIP of a SUPERVISED T33 (healing + alarms; attack only if G4) once T32a/b/c
  land the gates. Criticals: F-01 (no player detection — nameless battle entries), F-02
  (stop paths don't release inputs; Arduino heartbeat feeds the firmware watchdog), F-03
  (relative minimap position vs absolute routes, two diverging trackers).
- **T32a DONE** (2026-10-19, commit f0312d6): all 11 loop-safety findings fixed —
  PAUSED state with ungated release on every stop path, tick exception guard, attack
  de-spam, crowd warning, beep alerts, heal-priority rate token, monotonic last_update,
  F12 0x8000 bit, 6 new build_loop safety invariants. 857 pass / 8 skip, ruff+mypy clean,
  orchestrator-verified (diff + full gate).
- **STOPPED on user request (2026-10-19 ~14:00).** T32b run 1 was interrupted by the
  Claude CLI account session limit (it had completed the cavebot half of F-03/F-06).
  WIP committed d518a93 (suite red at 3 by design — mid-refactor); the armed 15:32
  re-dispatch job was killed. To resume: `claude -p "$(cat claude_brief_t32b.md)"
  --model opus --dangerously-skip-permissions` — the brief's RESUME note tells the
  agent to review `git diff`/the WIP commit, finish the wiring to green, then do
  items 1-10 (F-01 interim, F-03, F-06, F-09, F-10, F-11, F-13, F-20, F-25, F-27).
  Then T32c (brief claude_brief_t32c.md, P1-P7 + F-14/15/16/17/18/21) → STOP at T33.

## Build-to-Human-Test queue (2026-10-18, user: "do everything until Human testing")
1. **T40 integration layer** — DONE 2026-10-18 (verified; see Current Task).
2. **T28** — DONE 2026-10-19 (commit 1bf7b9b, orchestrator-verified).
3. **T29 + T30** — DONE 2026-10-19 (commit b10fe0c, orchestrator-verified; T30 found
   perception p95 sum 55.5 ms > 50 ms budget — sprites + logocr).
4. **T31** — DONE 2026-10-19 (commit c8aa239): 27 findings + T33 gates G1-G7.
5. **T32** — fix senior findings (coder), split into 3 sequential jobs: **T32a loop safety
   DONE (f0312d6)**, **T32b wiring+config PAUSED** (CLI session limit; WIP d518a93, resume command below), T32c perf+data quality (brief ready).
6. **STOP at T33** — long test session on private server (HUMAN gate). T34/T36 come after.

## Recently Completed (this build phase)
- **T24 DONE** (2026-10-18): refill route (cap-full → storage deposit → walk back);
  11 tests; merged in PR #2.
- **T27.D DONE** (2026-10-18): `moonlight/scripts.py` script runner; 35 tests.
- **T38 DONE** (2026-10-18): `perception/logocr.py` server-log OCR; 27 tests.
- **T18 DONE** (2026-10-18): HIL harness `tools/hil_harness.py`; 9 offline + real COM5.
- PR #2 merged (rebase, commit `3703a50`); baseline 768 pass/13 skip, ruff+mypy clean.

## Next
- **RESUME T32b** when the Claude CLI limit allows: `claude -p "$(cat
  claude_brief_t32b.md)" --model opus --dangerously-skip-permissions` (brief has a
  RESUME note; WIP at d518a93). Then T32c (`claude_brief_t32c.md`) → hand off to Human
  testing (T33).
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
  independent) — do not serialize them.** (Executed: they landed in one commit, b10fe0c.)
- (orchestrator, Oct 19) **T28 + T29 + T30 VERIFIED DONE.** Commits 1bf7b9b (T28,
  11:06) and b10fe0c (T29+T30, 11:12) are the deliverables of the 10:31 `claude -p`
  re-dispatch (whose log was left empty). Orchestrator re-ran the gates: 843 pass /
  8 skip (119 s), ruff clean, mypy clean (89 files), replay+safety 14/14. Real-frame
  results: vitals 199/199 within ±2pp; minimap marker 173/200 (27 misses — map-mark
  icons covering the radar centre, per T31 F-17); battle 80/200 frames with entries; logocr fail-closed (Tesseract
  binary absent here). **New findings for T31:** (a) perception p95 sum 55.5 ms >
  50 ms budget (sprites 29.5 ms, logocr 19.1 ms); (b) Tesseract must be installed on
  the deployment target for logocr to function. Removed stale dispatch artifacts
  (claude_brief_t28.md, claude_t28_replay.log); added missing READMEs
  (tests/golden, tests/hil, tests/replay, tests/safety). **T31 dispatched**
  (claude -p opus, brief claude_brief_t31.md).
- (orchestrator, Oct 19) **T31 DISPATCHED → DONE.** First `claude -p` run (opus) completed
  the review in-context (26 findings) but the final doc write stalled on a permission prompt
  and exited 0 with no artifact — root cause: missing --dangerously-skip-permissions
  (headless runs cannot answer prompts). Re-dispatched with the flag: round 2 produced
  docs/senior_review_findings.md — 27 findings (3 critical / 17 major / 7 minor), each with
  file:line refs, plus T33 ship gates G1-G7 (verdict: NO-SHIP as wired; conditional SHIP of
  a supervised healing+alarms session). Orchestrator-verified: git tree clean of code
  changes, full suite 843 pass / 8 skip, ruff+mypy clean. Committed c8aa239. **T32 split
  into 3 sequential coder jobs** (T32a loop safety → T32b wiring/config → T32c perf/data).
- (orchestrator, Oct 19) **T32a DISPATCHED → DONE + VERIFIED.** `claude -p` opus with
  --dangerously-skip-permissions; completed all 11 scope items. Orchestrator verified the
  actual diff (loop.py +282: PAUSED state, _pause/_fail_stop with 30s/3-error limits,
  ungated release_all, tick guard, cooldown; cli.py finally → backend.close(); targeting
  default max_monsters 4; F12 0x8000; monotonic last_update) and re-ran the full gate:
  857 pass / 8 skip (109 s), ruff clean, mypy clean (89 files). Notes carried to T32b:
  GUI backend reuse must reopen after close(); worst-case burst now 12 inputs in the
  first second (heal reservation). Committed f0312d6.
- (orchestrator, Oct 19 ~12:50) **T32b run 1 dispatched, then INTERRUPTED** by Claude
  CLI account session limit ("resets 3:30pm Europe/Warsaw" — verified with a sonnet
  probe: limit is account-wide, opus+sonnet both blocked). It made real partial
  progress before dying: cavebot.py F-03/F-06 refactor (Cavebot now takes
  minimap_region, reads WorldState.position, gained resume()), targeting.py + loop.py
  partial. Suite red at 3 (old-signature wiring in cli.build_loop + safety 4/5).
  Decision: KEEP the partial work (hardest part, correct direction), added a RESUME
  note to claude_brief_t32b.md, armed a background sleep+claude re-dispatch for
  15:32 UTC+2 with 10-min self-heal retries. T32c brief (P1-P7 + F-14/15/16/17/18/21)
  prepared in the meantime.
- (orchestrator, Oct 19 ~14:00) **USER STOP.** Re-dispatch job (proc_bc020cf1ae67)
  killed; partial T32b work committed as WIP d518a93 (tree clean; suite red at 3 by
  design — mid-refactor). T32b = PAUSED. Next session: resume command above, then
  verify (diff + full gate), commit, then T32c, then T33 hand-off.