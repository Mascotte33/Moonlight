# MOONLIGHT — Senior review findings (T31)

Reviewer: senior review pass, 2026-10-19 · Scope: whole repo at `19c3b01` · Review only (no code/test changes).

Baseline verified at review time (PATH `python` 3.14.7 — note: `.venv/Scripts/python.exe` named in the brief does **not** exist):
`python -m pytest -q` → **843 passed, 8 skipped**; `ruff check .` → clean; `mypy .` → no issues in 89 files.

## Executive summary

The code is clean, typed and well unit-tested, but it is **not ready for an unsupervised T33 server test as wired**: the production
perception path never resolves battle-list names, so player detection, player protection and loot triggering are dead (F-01),
and several stop/exception paths exit without releasing held inputs while the Arduino heartbeat keeps the firmware watchdog fed (F-02, F-04).
Cavebot, looting and refill cannot run against the only calibrated profile (missing regions/templates/routes, relative-vs-absolute coordinates — F-03, F-11).
Recommendation: **NO-SHIP as wired; conditional SHIP** of a supervised, healing + alarms (+ attack-with-fixes) session once the gates in the last section are met.

## Findings table

| ID | Severity | Area | Summary |
|----|----------|------|---------|
| F-01 | critical | Safety | Production BattleReader has no name resolver → every name is `""`, players never detected, player protection/alarm dead, attack key may target a player |
| F-02 | critical | Safety | Stop paths do not release inputs: kill release is focus-gated, `!screen_ok` stop / `_shutdown` / UI+GUI stop never call `release_all`; heartbeat keeps watchdog fed |
| F-03 | critical | Architecture | Minimap tracking is relative from (0,0,0) but routes use absolute coords; cavebot and perceive run two diverging trackers |
| F-04 | major | Safety | Any exception in perceive/backend/module escapes `tick()` and ends `run()` without releasing inputs |
| F-05 | major | Architecture | One unfocused tick or one bad frame stops the run permanently (no recovery, no alert) |
| F-06 | major | Architecture | Cavebot "stuck" timer runs while ATTACK/LOOT owns the tick → route halts after any fight > 5 s |
| F-07 | major | Safety | ATTACK presses the attack key on every token (~10/s) even with a valid target already selected |
| F-08 | major | Architecture | `max_monsters_to_engage=1` default: 2+ monsters → "flee" → ALARM, which has no flee action → bot stalls while being hit |
| F-09 | major | Architecture | Loot and refill gates exist but are unreachable: loot trigger needs names, refill needs `capacity` which nothing perceives |
| F-10 | major | Architecture | Buffs: `state.buffs` never perceived → recast every cooldown forever; default spell list is wrong (exori = attack spells) |
| F-11 | major | Data | Only profile lacks `game_grid`/`cursor`/`loot_window`; no `data/templates`, no routes; `BotConfig.default()` names a non-existent profile |
| F-12 | major | Safety | No user-facing alarm anywhere; cavebot/refill alarms never read by the loop; PM detection not wired |
| F-13 | major | Architecture | GUI "Start bot" builds a loop with no perceive/modules (stops on tick 1) and silently falls back to FakeInput; `PerceptionWriter` forces `client_focused=True` |
| F-14 | major | Performance | Clicker: up to 6 synchronous full-frame captures per click, no settle delay; cursor may not be capturable at all |
| F-15 | major | Test coverage | T30 timing test cannot fail, excludes capture, and sums readers not on the production tick |
| F-16 | major | Data | Battle region is 58 px high → at most 2 rows ever read; monster HP never read |
| F-17 | major | Data | Minimap marker missed on 27/200 frames (map-mark icons over centre); tracker silently drops large shifts |
| F-18 | major | Test coverage | Golden labels are placeholders for everything but HP/MP → battle/sprite/minimap/loot replay checks are vacuous |
| F-19 | major | Test coverage | Safety suite never exercises the production wiring (`cli.build_loop`, `make_perceive`, `focus_check`) |
| F-20 | major | Safety | Cavebot, clicker and refill inputs bypass the 10/s rate limiter |
| F-21 | minor | Data | LogOCR: silent fail-closed, two Tesseract subprocesses + 2× cubic upscale per read, binary undocumented (unwired → safe today) |
| F-22 | minor | Architecture | Single shared rate token: heals can wait ~100 ms behind attack; a no-op loot step burns a token |
| F-23 | minor | Architecture | `last_update` written with wall clock, documented as monotonic |
| F-24 | minor | Architecture | Duplicated HP thresholds (loop 30/60 vs healing 30/70) and duplicated HP-drop logic |
| F-25 | minor | Architecture | Cavebot `start()` always resets to waypoint 0 (after refill too) |
| F-26 | minor | Safety | F12 monitor reads `GetAsyncKeyState & 0x0001` (unreliable "pressed since last call" bit) |
| F-27 | minor | Architecture | Repo hygiene: `customtkinter` missing from deps, `.venv` missing, stale perf/instructions docs, stray root `__pycache__`/package dirs |

**Totals: 27 findings — 3 critical / 17 major / 7 minor.**

---

## Architecture

### F-03 — Relative minimap position vs absolute routes; two trackers (critical)
- **Files:** `moonlight/perception/minimap.py:186-189` (anchor `Position(0,0,0)`), `moonlight/cli.py:80` and `moonlight/cli.py:177` (two independent `MinimapReader`s), `moonlight/decision/cavebot.py:299,321-327`.
- **Description:** `MinimapReader` is built without `initial_position`/`map_matcher`, so positions are offsets from wherever the bot started. Waypoints (and `tests/safety` routes) are absolute Tibia coordinates (e.g. 100,100,7). The cavebot owns a second reader fed by its own capture, so `state.position` and the cavebot's position drift independently.
- **Risk:** The cavebot walks toward a target hundreds of tiles away, clamps clicks to the radar edge (`cavebot.py:377-382`) and walks in a straight line until "stuck"/"lost". With a real route this means uncontrolled movement on a live server.
- **Fix:** Single tracker owned by perception; seed with an absolute start position (config/CLI arg) or a map matcher; cavebot reads `state.position` instead of re-capturing.
- **T32 scope:** M — wiring + one config field; plus a replay test asserting absolute positions on one labelled session.

### F-05 — Alt-tab or one bad frame stops the bot for good (major)
- **Files:** `moonlight/cli.py:83-96`, `moonlight/loop.py:487-492`.
- **Description:** `make_perceive` maps "not focused" *and* any exception to `screen_ok=False`; the loop then sets `_stop_event` and returns. There is no grace window, no retry, no resume, no notification. Fail-closed is right; fail-*permanent* is not.
- **Risk:** In T33 the human will alt-tab once and the bot silently stops (healing included) while the character stays in a hunting spot — strictly worse than pausing.
- **Fix:** Distinguish *pause* (unfocused / transient error: drop inputs, keep ticking, log) from *stop* (N consecutive failures or > X s). Release inputs on entering pause.
- **T32 scope:** S — loop state `PAUSED`, counter, 2 tests.

### F-06 — Cavebot stuck timer counts fight time (major)
- **Files:** `moonlight/decision/cavebot.py:283-288`, `moonlight/loop.py:536-546`.
- **Description:** `_last_move` only updates inside `step()`, which only runs in MOVE. ATTACK/LOOT win priority, so after a > 5 s fight the first MOVE tick sees `now - _last_move > 5 s` with an unchanged position and halts with `"stuck"` (`_halt`, L316-319 → silently inactive).
- **Risk:** Route stops after the first real fight; the bot idles in the spawn.
- **Fix:** Reset `_last_move` when MOVE is (re)entered, or have the loop call a `cavebot.pause()/resume()`.
- **T32 scope:** S.

### F-08 — Default flee threshold stalls the bot in ALARM (major)
- **Files:** `moonlight/decision/targeting.py:73` (`max_monsters_to_engage = 1`), `:174-187`; `moonlight/loop.py:551,557-558,687-696`.
- **Description:** Two monsters in the battle list → FLEE → `_is_alarm` → ALARM. `_act_alarm` only heals; there is no flee movement and no attack. On the golden set the reader sees 2 entries on 21/200 frames.
- **Risk:** In any spawn with 2 monsters the bot stops attacking and stands still while being hit (healing continues until mana runs out).
- **Fix:** Default `max_monsters_to_engage` to a realistic value (e.g. 4–6), and make "too many monsters" a *warning* that keeps attacking unless a real flee route exists.
- **T32 scope:** S (default + test); real flee = later task.

### F-09 — Loot and refill gates are enforced but unreachable (major) — brief item 5
- **Files:** `moonlight/loop.py:511-519,541-544,733-759`; `moonlight/decision/looting.py:79-104`; `moonlight/decision/refill.py:117-126,162-182`.
- **Description (verified):**
  - *Loot-window focus gate:* enforced twice — `Looting.step` returns False when unfocused (`looting.py:88`) and the loop's `_send` drops all actions unless `client_focused and screen_ok` (`loop.py:670`). Safety test 11 covers the module-level gate. **But** `on_target_died` is only called when `prev` (the last selected target's *name*) is truthy (`loop.py:518`); with names always `""` (F-01) it never fires → looting never starts.
  - *Refill cap gate:* enforced — `_decide_state` only enters REFILL via `should_refill` (`loop.py:543`), which requires `capacity.max_weight > 0` and weight ≥ trigger (`refill.py:117-126`). **But** no perception writes `state.capacity` (only GUI tabs read it), so REFILL is unreachable in production. Safety test 12 injects `Capacity` by hand.
- **Risk:** Low safety risk (fails closed), but the T33 plan cannot exercise loot/refill; the passing tests give false confidence.
- **Fix:** Name resolution (F-01) for loot; a capacity reader (inventory cap OCR/digits) for refill. Until then, refuse to enable looting/refill in `BotConfig.validate`.
- **T32 scope:** S (validate guard); M–L (capacity reader).

### F-10 — Buffs recast forever; wrong default spells (major)
- **Files:** `moonlight/decision/buffs.py:111-119` (DEFAULT_BUFFS: `utemo`, `exori flamatio/visatio/terratio/sanatio`), `:133` (`cast_cooldown_ms=1000`).
- **Description:** `state.buffs` is never written by perception, so every buff is always "missing" and is recast every cooldown. `exori *` are attack spells, not buffs; `utemo` is not a real spell.
- **Risk:** If enabled: constant spell spam (mana drain, very bot-like pattern). Disabled by default — keep it that way for T33.
- **Fix:** Buff-icon perception or time-based recast from real durations; correct spell list.
- **T32 scope:** S (fix defaults + forbid enabling without perception).

### F-13 — GUI Start bot is non-functional and can silently go "fake" (major)
- **Files:** `moonlight/gui/app.py:141-166` (L157-162 FakeInput fallback, L164 bare `GameLoop`), `moonlight/state.py:98-99` (defaults `screen_ok=False`), `moonlight/gui/state_bridge.py:400-501` (L476 `client_focused = True`), `moonlight/ui.py:674-677`.
- **Description:** The GUI builds `GameLoop(state, backend, cfg.to_loop_config())` without `perceive` or modules → first tick sees `screen_ok=False` and stops. A missing Arduino is only a log warning. `PerceptionWriter` (never instantiated) duplicates `make_perceive` and hard-codes focus True — wiring it as-is would bypass the focus gate.
- **Risk:** The operator thinks the bot runs (or runs with hardware) when it does neither; a future "quick fix" wiring `PerceptionWriter` disables a safety gate.
- **Fix:** GUI should call `cli.build_loop` with the real capture/focus; hardware fallback requires an explicit "simulation" toggle; delete `PerceptionWriter`.
- **T32 scope:** M. **For T33 use the CLI only.**

### F-22 — Shared single-token rate limiter (minor)
- **Files:** `moonlight/loop.py:384,733-741`.
- **Description:** Capacity-1 bucket shared by heal/attack/loot/buffs: a heal can wait up to 100 ms behind an attack press. `_act_loot`'s `step()` returns None → counted as sent and consumes a token even when nothing was pressed.
- **Fix:** Priority reservation for heals (or separate bucket); `step` should return `looting.step(snap)`.
- **T32 scope:** S.

### F-23 — Wall clock written into a monotonic field (minor)
- **Files:** `moonlight/cli.py:109` vs `moonlight/state.py:27,118`.
- **Fix:** `time.monotonic()`. **T32 scope:** XS.

### F-24 — Duplicated thresholds and HP-drop logic (minor)
- **Files:** `moonlight/loop.py:134-140` (critical 30 / normal 60) vs `moonlight/decision/healing.py:84-87` (30 / 70); HP-drop detection in both `loop.py:~600-621` and `decision/alarms.py`.
- **Risk:** State label says IDLE/ATTACK while healing casts at 61-70 %; tuning one place doesn't tune the other.
- **Fix:** Loop derives heal states from the healing module when wired. **T32 scope:** S.

### F-25 — Cavebot restarts from waypoint 0 (minor)
- **Files:** `moonlight/decision/cavebot.py:256-261`, `moonlight/loop.py:757`.
- **Description:** After refill (or any restart) the route starts at waypoint 0 rather than the nearest waypoint. **Fix:** `start(nearest=True)`. **T32 scope:** S.

### F-27 — Repo hygiene / environment drift (minor)
- **Files:** `pyproject.toml` (no `customtkinter`, GUI imports it), missing `.venv` (brief + docs reference `.venv/Scripts/python.exe`), `docs/perf_profile.md` (claims perception on its own thread; measured 1280×960 capture), root-level `__pycache__/`, `decision/`, `perception/`, `gui/` stray dirs, `CalibrationError` (a `RuntimeError`) not caught by `cli.main` (`cli.py:342`) → traceback instead of the friendly error when e.g. cavebot is enabled without `game_grid`.
- **Fix:** Add dep, document interpreter, refresh docs, delete stray dirs, catch `CalibrationError`. **T32 scope:** XS–S.

## Safety

### F-01 — Players are never detected in production (critical)
- **Files:** `moonlight/cli.py:78` (`BattleReader(profile)` — no `name_resolver`, no `monster_names`), `moonlight/perception/battle.py:309-319` (`_resolve_name` → `""`, `_is_player("") → False`), `:366-376` (`entries_to_state`), `moonlight/decision/targeting.py:156-167,214-216`, `moonlight/loop.py:551`.
- **Description:** Every battle entry is a nameless "monster". `players_on_screen` is always 0, so player protection (targeting flee), the loop's player ALARM and any player-based alarm never fire. A player standing in the battle list is counted as a monster and the attack key (which takes the client's *next* target, `loop.py:724-726`) may select and attack them.
- **Risk:** Unintended PvP / skull, no reaction to other players (the main anti-detection signal), loot never triggers (F-09). This is the single most important safety gap.
- **Fix:** Wire a name resolver (OCR or template font) + monster list from config into `make_perceive`; until then treat *any* entry with unknown name as "unknown → do not auto-attack", or disable targeting.
- **T32 scope:** M–L (resolver) / XS (interim: targeting disabled when no resolver, enforced in `BotConfig.validate`/`build_loop`).

### F-02 — Inputs not released on stop (critical)
- **Files:** `moonlight/loop.py:681-685` (kill release goes through `_send` → dropped when unfocused/`!screen_ok`), `:488-492` (`!screen_ok` stop without release), `:438-465` + `:780-791` (`_shutdown` has no `release_all`), `moonlight/ui.py:337-340` (`BotController.stop`), `moonlight/gui/app.py:169-…,201-208`; `moonlight/cli.py:248-249` is the **only** release; `moonlight/input.py:699-719` (heartbeat), `firmware/moonlight/moonlight.ino:414,463` (any line, incl. heartbeat `P`, refreshes the 10 s watchdog — `firmware/instructions.md:25`).
- **Description:** Pressing F12 while the Tibia window is not focused (the most likely moment to hit F12) sends **nothing** — `_act_kill` counts it as "dropped unfocused". Normal stop, screen loss and GUI stop rely on the caller. Because the heartbeat thread keeps pinging, the firmware watchdog never fires while the Python process lives.
- **Risk:** A held key/mouse button (e.g. mid-`press_combo` modifier, drag in a clicker move) stays held after "stop"; kill switch appears to fail.
- **Fix:** `_act_kill` and `_shutdown` call `self.input.release_all()` directly (ungated, exceptions swallowed+logged); stop the heartbeat when the loop stops; `close()` the backend in the CLI `finally`.
- **T32 scope:** S — 3 call sites + 3 tests (kill while unfocused, `!screen_ok`, exception).

### F-04 — Exceptions escape the tick without release (major)
- **Files:** `moonlight/loop.py:451-465,483-484` (unguarded `self._perceive()`), `moonlight/input.py:635-650` (`_send_command` raises `ArduinoInputError`/`DeviceLostError`), module `step()` calls (cavebot frame/minimap, clicker).
- **Description:** `make_perceive` catches its own errors, but a backend error (serial timeout, device lost) or any module exception propagates through `tick()`; `run()`'s `finally` only stops the F12 monitor. CLI `finally` releases, GUI/UI threads do not.
- **Risk:** Crash mid-combo with keys held (see F-02); in the GUI the thread dies silently.
- **Fix:** `try/except` around `tick()` in `run()` → log, `release_all`, stop (or pause per F-05).
- **T32 scope:** S.

### F-07 — Attack key spam (major)
- **Files:** `moonlight/loop.py:718-731`, `moonlight/decision/targeting.py:189-196`.
- **Description:** While in ATTACK the loop presses `attack_combo` whenever a token is available — ~10 presses/s for the whole fight, even when `selected_target` is already the valid target. In Tibia the attack/next-target key *cycles* targets, so this also flips targets.
- **Risk:** Obvious bot signature; target thrashing; with F-01 increases the chance of selecting a player.
- **Fix:** Press only when `snap.selected_target is None` (or target changed), with a ≥ 1 s re-press cooldown.
- **T32 scope:** XS–S.

### F-12 — No operator alert; module alarms ignored (major)
- **Files:** `moonlight/loop.py:687-696` ("audible/visual alarm is T25's job"), `moonlight/decision/alarms.py:200-217,331` (PM check only injectable), `moonlight/cli.py:204-212` (no `pm_open_check`), `moonlight/decision/cavebot.py:316-319`, `moonlight/decision/refill.py:190-201`.
- **Description:** No `winsound`/beep/notification anywhere. `cavebot.alarm` / `refill.alarm` are set but never read by the loop — the bot just goes IDLE. PM detection isn't wired.
- **Risk:** In a supervised test the human won't notice stalls/alarms unless watching the log.
- **Fix:** Loop surfaces module alarms as ALARM + one alert sink (beep + log line); wire a PM check or document it as absent.
- **T32 scope:** S.

### F-20 — Rate limiter bypasses (major)
- **Files:** `moonlight/loop.py:759` (refill), `:769` (move/cavebot), `moonlight/decision/cavebot.py:384-398` (tile actions via `input_backend`), `moonlight/clicker.py` (moves/clicks inside one `_send`).
- **Description:** One "action" through `_send(..., rate_limited=False)` can issue many serial commands (clicker iterations, rope/shovel hotkey + click). The 10/s invariant tested by safety test 9 holds only for heal/attack/loot/buffs.
- **Fix:** Rate-limit at the backend (count serial commands), or give the clicker its own bounded budget.
- **T32 scope:** S–M.

### F-26 — F12 uses the wrong GetAsyncKeyState bit (minor)
- **Files:** `moonlight/loop.py:112-121`.
- **Description:** `state & 0x0001` is the "pressed since last call" bit, which Microsoft documents as unreliable (shared across processes). A press can be missed.
- **Fix:** `state & 0x8000` (currently down) — the 1 ms poll is fast enough. **T32 scope:** XS.

## Performance

Measured facts (T30 report on this machine) and what they mean:

| Item | Measured / derived | Note |
|------|-------------------|------|
| Perception p95 sum | 55.5 ms (sprites 29.5, logocr 19.1) | **Misleading:** sprites, logocr, cursor and loot are *not* on the production tick (`cli.py:61-111` runs vitals, battle, minimap, floor only). |
| Production perception (vitals+battle+minimap+floor) | ≈ 55.5 − 29.5 − 19.1 − cursor − loot ≈ < 7 ms p95 | Comfortably in budget. |
| LogOCR 19.1 ms | Only cubic upscale + an immediate `TesseractNotFoundError` | With Tesseract installed: two subprocess spawns per read ⇒ realistically **100–400 ms** per call. |
| Full-window capture 2560×1369 | **not measured** (T30 uses a preloaded frame; `perf_profile.md` measured 1280×960 at 6.8 ms) | Likely 15–30 ms with mss at this size; repeated per extra consumer. |
| Captures per tick | 1 (perceive) + 1 (cavebot `_read_position`, `cavebot.py:321-327`) + up to 6 (clicker) | Worst case ~8 full-frame grabs in one tick. |
| Serial ACK wait | up to 0.5 s per command (`input.py`, read_timeout) | Synchronous on the tick thread. |

### F-14 — Clicker: synchronous captures, no settle delay, cursor visibility unverified (major)
- **Files:** `moonlight/clicker.py:60-64` (tolerance 2, max_iters 5), `click_at` (~L105-165), `moonlight/cli.py:164-167`.
- **Description:** Each click = move → full capture → template match, up to 6 captures inside one tick, no settle delay between move and re-capture (reads the pre-move frame → overshoot/oscillation). mss/DXGI normally exclude the hardware cursor from captures; if so `CursorReader` never finds it and every click fails closed. No cursor templates exist (`data/templates` absent).
- **Risk:** Ticks of 100–300 ms (heals delayed) and/or cavebot/loot that never clicks.
- **Fix:** HIL-verify cursor visibility first; if invisible, switch to open-loop absolute positioning with calibrated scaling. Otherwise: one move+capture per tick (state machine), 15–30 ms settle, capture only a small ROI around the target.
- **Expected saving:** worst-case tick from ~6 × capture (~100–180 ms) to ≤ 1 ROI grab (~1–2 ms).
- **T32 scope:** M.

### F-15 — Timing test is misframed and can't fail (major — test coverage, listed here for budget)
- **Files:** `tests/perf/test_timing_report.py:166-246` (only assert `tick_mean > 0`; L205 "capture (preloaded frame)"; L181-198 sums non-production readers; L113-128 mocked cavebot minimap).
- **Fix:** Measure the real `make_perceive` + `ScreenCapture.get_frame` at native resolution, include cavebot step with a real frame provider; assert p95 < 50 ms (skip on CI without a display).
- **T32 scope:** S.

### Budget fixes with expected savings

| # | Fix | Where | Expected saving |
|---|-----|-------|-----------------|
| P1 | Capture once per tick; share the frame with cavebot/clicker/loot (frame cache keyed by tick) | `cli.py:173-196`, `cavebot.py:321-327`, `clicker.py` | −1 full capture per cavebot tick (~15–30 ms at 2560×1369) and −1..6 per click |
| P2 | Capture only the union of calibrated regions (or per-region grabs) instead of the full window | `capture.py:get_frame` | full grab ~15–30 ms → regions (~0.4 MP) ~3–5 ms |
| P3 | Sprites: run on demand only (no battle-list confirmation available), on a 2× downscaled ROI around the player, max N templates | `perception/sprites.py:254-290` | 29.5 ms → ~3–5 ms (4× fewer pixels, ROI ≈ 1/4 of grid); 0 ms on ticks it doesn't run |
| P4 | LogOCR off-tick worker ≤ 1 Hz; single `image_to_data` call (text + conf from one run); drop 2× INTER_CUBIC upscale (or INTER_LINEAR on a threshold image); only the last 2–3 log lines | `perception/logocr.py:77-82,116-132` | removes 100–400 ms/tick worst case once Tesseract is installed (→ 0 ms on tick); ½ the subprocess cost per read |
| P5 | Clicker as per-tick state machine with settle delay (F-14) | `clicker.py` | worst-case tick −100–180 ms |
| P6 | Serial: don't block on ACK for fire-and-forget keys (or shorter timeout + async ACK check) | `input.py` `_send_command` | removes up to 500 ms stalls on a flaky link |
| P7 | Make T30 assert the real budget (F-15) | `tests/perf/test_timing_report.py` | prevents regressions; no runtime saving |

With P1+P2 the production tick (capture + 4 readers + decision) should sit around 10–15 ms p95, leaving room for on-demand sprites.

## Data / label quality

### F-11 — Profile and data are incomplete for anything beyond vitals/battle/minimap (major)
- **Files:** `moonlight/profiles/moonlight_user.json` (regions: hp_bar, mp_bar, battle_list `[2387,597,156,58]`, minimap, floor_indicator only), `data/` (only `waypoints/README.md`), `moonlight/config.py:120-130` (`profile="default"`, does not exist), `moonlight/cli.py:164,182,193`.
- **Description:** No `game_grid` → enabling cavebot raises `CalibrationError` (uncaught, F-27); no cursor/loot templates → clicker/loot empty; no routes. `moonlight run` with default config fails on profile "default".
- **Fix:** Calibrate missing regions, ship templates, set the default profile to `moonlight_user` (or require it), and make `check` report missing regions per enabled module.
- **T32 scope:** S (config/check) + calibration session (human).

### F-16 — Battle list read is capped at 2 rows; no HP (major) — brief item 3
- **Files:** profile `battle_list` height 58; `moonlight/perception/battle.py:246-256` (22 px slots, top padding 11 → rows at 11 and 33; row 3 starts at 55 with 3 px → fails HP-bar check); `:258-265` (`hp_percent` never set → 100 in `entries_to_state`, L367).
- **Measured on 200 golden frames:** 0 entries on 120, 1 on 59, 2 on 21 → **80 frames with entries, 67 with a selected target**, never more than 2. Labels say 0 monsters on every frame (placeholders), so precision/recall cannot be computed.
- **Risk:** Monster count is under-reported (≥ 3 monsters looks like 2), targeting "lowest HP" logic has no data.
- **Fix:** Recalibrate the battle list to its full height; read the HP bar fill per row; label `monster_count`/`selected_target` for the golden set.
- **T32 scope:** S (region + HP fill) + labelling (human).

### F-17 — Minimap marker misses: 27/200, cause = map marks (major) — brief item 2
- **Files:** `moonlight/perception/minimap.py:90,93,222-223,238-248,272-287`.
- **Measured:** marker found on 173/200 frames ⇒ **27 misses** (the brief's "17" is an arithmetic slip). Misses cluster in whole sessions: 230513101 f3-6, 231254331 f1-4, 233204608 f2-6, 235316830 f1-4, 001144414 f1-6, Old Wrath 011233197 f3-4, Kielbasa TreasureFound_6, Quattro SkillUp. Visual inspection: user map-mark icons (tent/green triangle, sword/flag) cover the radar centre and hide the white cross (5×5 white check, tol 60).
- **Also:** shifts > radar/2 are rejected and silently treated as 0 (L238-248), so a fast move or teleport becomes a silent position error; no confidence is propagated.
- **Risk:** Cavebot gets `None` positions → stuck alarms; silent drift after teleports/stairs.
- **Fix:** (a) operator instruction: no map marks near hunting routes; (b) validate on radar-frame/shift response instead of the centre cross; (c) continue tracking through occlusion (phase correlation doesn't need the cross); (d) on rejected shift return None/flag, and re-anchor.
- **T32 scope:** S–M.

### F-21 — LogOCR / Tesseract dependency (minor) — brief item 4
- **Files:** `moonlight/perception/logocr.py:64-68` (catch-all → `LogFrame()`), `:77-82`, `:89-91`, `:116-132`; `pyproject.toml` (pytesseract dep, binary not documented).
- **Description:** With the binary missing every read returns `ok=False` — verified on 200 frames (0 OCR successes). The fail-closed path is **safe today** because LogOCR is not wired into production (only a comment at `loop.py:513-514`). Problems for when it is wired: no log line/reason on failure (indistinguishable from "empty log"), two Tesseract subprocesses per read, dedupe by exact text drops repeated identical events (two "You loot nothing" lines → one), Tesseract install not in README.
- **Fix:** Log once on first failure with the exception; health check in `moonlight check`; P4 above; dedupe by (line, position) not text.
- **T32 scope:** S.

## Test coverage

### F-18 — Golden labels are placeholders (major)
- **Files:** `tests/golden/labels.json` (verified: all 200 frames `monster_count=0`, `selected_target=null`, `players_on_screen_count=0`, position x/y/z null), `tests/replay/test_replay_perception.py:56-57` (`MINIMAP_MAX_STEP=53` = tracker's own rejection limit → vacuous), `:152-158`, `:179-182` (label-gated → inactive), `:98-108` (cursor/loot templates cut from the frames themselves; loot probe matches every frame, L190-191).
- **Description:** Only HP/MP have real truth (199/199 within ±2 pp). Every other replay check is "no exception + type check".
- **Risk:** Regressions in battle/minimap/sprites ship green; T28's "replay coverage" overstates confidence.
- **Fix:** Label `monster_count`, `selected_target`, `players_on_screen_count` (fast, visual) and positions for 2–3 sessions; tighten `MINIMAP_MAX_STEP` to the measured ~8 tiles.
- **T32 scope:** human labelling + S test change.

### F-19 — Safety suite bypasses the production wiring (major)
- **Files:** `tests/safety/test_safety_suite.py:42-66` (hand-set `client_focused`/`screen_ok`, `GameLoop` built directly), `:172-187` (test 6 asserts the *backend's* `close()` releases, not the loop — and the CLI never calls `close()`), `:274-309` (test 12 injects `Capacity`).
- **Description:** None of the 12 invariants runs through `cli.build_loop` + `make_perceive` + `focus_check`. That is exactly why F-01, F-02 (kill while unfocused), F-05 and F-09 are invisible to the suite.
- **Fix:** Add a `build_loop` variant of the invariants with `FakeInput`, golden frames and a toggleable `focus_check`: kill while unfocused releases; stop releases; exception releases; player in battle list → ALARM.
- **T32 scope:** S–M.

### (F-15 — see Performance)

Other observations (no separate finding): unit coverage of decision modules is good; ruff/mypy are clean; `tests/perf` is marked `slow` but still runs in the default suite (≈ 112 s total).

---

## Ship / no-ship for T33

**Verdict: NO-SHIP as currently wired.** **Conditional SHIP** for a *supervised* T33 session limited to healing + alarms (+ attack only if G4 is done), once all gates below are met:

| Gate | Condition | Findings |
|------|-----------|----------|
| G1 | Kill/stop/exception/`!screen_ok` all call `release_all` ungated; heartbeat stops with the loop; CLI `close()`s the backend; tests via `build_loop` | F-02, F-04, F-19 |
| G2 | Targeting either has working player detection, or is forced off / restricted to "no auto-attack on unknown names" | F-01 |
| G3 | Unfocus = pause (inputs dropped + released), not permanent stop; operator gets an audible alert on stop/alarm | F-05, F-12 |
| G4 | (if attack enabled) attack key pressed only when no valid target is selected; `max_monsters_to_engage` default raised | F-07, F-08 |
| G5 | Cavebot, looting, refill, buffs **disabled** in the T33 config (`validate` refuses them until F-03/F-06/F-09/F-10/F-11/F-14 are resolved) | F-03, F-06, F-09, F-10, F-11, F-14 |
| G6 | CLI only (no GUI Start), default config points at `moonlight_user`, Arduino required (no FakeInput fallback) | F-11, F-13 |
| G7 | 10-minute HIL dry run on a safe spot: F12 while unfocused releases a held key; alt-tab pauses and resumes; capture-at-2560×1369 tick p95 < 50 ms logged | F-02, F-05, F-15 |

Minor findings (F-21..F-27) do not gate T33 except F-26 (F12 bit), which is an XS fix and should ride along with G1.
