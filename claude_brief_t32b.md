You are the coder for the MOONLIGHT Tibia bot repo at C:\Users\kamil\moonlight.
This is task **T32b — wiring + config fixes** from the T31 senior review.
## ⚠️ RESUME NOTE (orchestrator, 2026-10-19 ~12:55)
A previous run of this brief was interrupted mid-work by an account session limit.
Uncommitted PARTIAL changes remain in the working tree:
- `moonlight/decision/cavebot.py` — F-03/F-06 refactor completed IN THE MODULE:
  `Cavebot.__init__` now takes `minimap_region: Rect` (was `MinimapReader` +
  `FloorReader` + `frame_provider`), reads position from `WorldState.position`
  (perception owns the single tracker), and gained a `resume()` method
  (stuck-timer reset when MOVE is re-entered — F-06).
- `moonlight/decision/targeting.py`, `moonlight/loop.py` — related partial
  changes; review everything with `git diff` first.
The suite is currently RED (3 failures): `test_headless_smoke_all_modules`
(TypeError — `cli.build_loop` still wires the old Cavebot signature) and safety
tests 4/5 (cavebot stuck/lost).
**Start here:** `git diff` → finish the interrupted refactor (`cli.build_loop`
wiring, remaining callers, tests) → suite green → then proceed through items
1-10 in order. Items 2 (F-03) and 3 (F-06) may already be partially done —
verify rather than redo. If a part of the partial work goes in a wrong
direction, fix it in place; do not revert everything.


## Read first (source of truth)
- `docs/senior_review_findings.md` — findings doc. Each item below has file:line refs,
  Description, Fix, and a "T32 scope" line there. Read it fully before coding.
  Your job is the part of the T33 ship gates that belongs to wiring/config: **G2, G5, G6**.
- `PROGRESS.md` (status; note T32a just landed: PAUSED state, tick error guard,
  heal-priority rate token, `backend.close()` in the CLI `finally`), `instructions.md`,
  and the `instructions.md` files inside `moonlight/`, `tests/`, `gui/` if present.
- `moonlight/cli.py`, `moonlight/config.py`, `moonlight/profile.py`,
  `moonlight/decision/targeting.py`, `moonlight/decision/buffs.py`,
  `moonlight/decision/cavebot.py`, `moonlight/perception/minimap.py`,
  `gui/app.py`, `pyproject.toml`, `tests/` (unit, safety, replay).

## Context from T32a (already merged, do not re-fix)
- Loop now has a PAUSED state (pause-not-stop on unfocus), tick exception guard,
  attack re-press cooldown, heal-reserved rate token (worst case 12 inputs in the
  first second), `beep_alert` sink, and 6 new build_loop safety tests in
  `tests/safety/test_safety_suite.py` (tests 13-18) — all must keep passing.
- The CLI `finally` now calls `backend.close()` (releases inputs + stops the Arduino
  heartbeat). **If the GUI restarts the loop it must reopen the backend.**
- T32a left a note: GUI backend reuse needs reopening after shutdown — handle it in
  your F-13 work.

## Conventions (non-negotiable)
- Python 3.11+ style, type hints everywhere; no `print()` in production code (logging only).
- `ruff check .` and `mypy .` MUST stay clean.
- Tests in `tests/` subdirs. Full suite: `python -m pytest -q` (baseline: 857 passed,
  8 skipped). Use system `python` (3.14.7) — there is NO `.venv` in this repo.
- Do NOT commit. The orchestrator commits after verifying.
- Budget: 15-40 minutes. If you run low on context, stop and report done vs remaining.

## Scope — fix exactly these 10 findings
1. **F-01 interim (critical, XS)** — no auto-attack with unknown names: with no name
   resolver wired, every battle entry is a nameless "monster", so targeting must treat
   unknown entries as *unknown → do not auto-attack*. Enforce in `BotConfig.validate`
   / `build_loop`: targeting enabled without a resolver is an explicit warning at start
   (documented, visible), never a silent bypass. Player detection stays 0 until a
   resolver exists — that is T34 (human calibration + OCR), not this job.
2. **F-03 (critical, M)** — one position tracker: perception owns the tracker;
   `MinimapReader` is built with an absolute start position seeded from a new config
   field (and/or CLI arg) instead of "offset from wherever the bot started"; cavebot
   reads `state.position` instead of running a second tracker. Add a replay test that
   asserts absolute positions on one labelled golden session (labels exist in
   `tests/golden/`).
3. **F-06 (major, S)** — cavebot stuck timer: reset `_last_move` when MOVE is
   (re)entered so a >5 s fight doesn't trigger a false stuck halt on the first MOVE.
4. **F-09 (major, S)** — unreachable gates become honest gates: `BotConfig.validate`
   refuses to enable looting without a monster-name perception source and refill
   without a capacity perception source (clear error message naming what's missing).
   The capacity reader itself is M-L and deferred to T34 — the guard is what ships.
5. **F-10 (major, S)** — buffs: fix the default spell list (`exori *` are attacks,
   `utemo` is not real — remove), default OFF, and `validate` forbids enabling buffs
   without buff-icon perception (clear error).
6. **F-11 (major, S)** — profile completeness: default profile becomes
   `moonlight_user` (or `run` requires one — pick one, document it); the check command
   reports missing calibration regions PER ENABLED MODULE (module → OK/MISSING list)
   instead of a generic failure. The actual calibration session is human work (T33 HIL)
   — you ship the reporting, not the pixels.
7. **F-13 (major, M)** — GUI Start bot: the GUI must build the loop through
   `cli.build_loop` with real capture/focus (not a bare `GameLoop` with no perceive);
   missing Arduino requires an explicit "simulation" toggle (no silent FakeInput);
   delete `PerceptionWriter`; handle backend re-open on loop restart (T32a note);
   document in the GUI that **T33 uses the CLI**. `tests/unit/test_ui.py` smoke must
   keep passing (headless-safe).
8. **F-20 (major, S-M)** — rate-limit at the backend: count actual serial commands
   (one "action" via `_send(..., rate_limited=False)` can issue many serial commands —
   clicker iterations, hotkey+click). Keep the 10/s invariant true for the wire, and
   update safety test 9 (which T32a changed to a 12/s heal scenario) to match.
9. **F-25 (minor, S)** — cavebot `start(nearest=True)`: after refill/restart, resume
   from the nearest waypoint, not waypoint 0.
10. **F-27 (minor, XS-S)** — repo hygiene, all from the doc: add `customtkinter` to
    `pyproject.toml` deps; document the real interpreter (system `python` 3.14.7, no
    `.venv`) wherever docs reference `.venv/Scripts/python.exe`; refresh
    `docs/perf_profile.md` (it wrongly claims perception on its own thread and a
    1280×960 capture); delete the stray root-level dirs `__pycache__/`, `decision/`,
    `perception/`, `gui/` (the real packages live under `moonlight/` — verify each
    stray dir is truly stray before deleting); catch `CalibrationError` in
    `cli.main` (cli.py:342) → friendly one-line error, no traceback.

## Out of scope (T32c or later — note them in your DONE message if you trip over them)
P1-P7 perf fixes, F-01 full name resolver, F-02/F-04/F-05/F-07/F-08/F-12/F-19/F-22/
F-23/F-24/F-26 (done in T32a), F-14, F-15, F-16, F-17, F-18, F-21, and any perception
reader changes beyond the minimap seed param in item 2.

## Definition of done
- All 10 items implemented.
- `python -m pytest -q` → 0 failures (baseline 857 passed / 8 skipped; may grow).
- `ruff check .` clean; `mypy .` clean.
- `moonlight run --help` exits 0; the validate guards fire with clear messages
  (targeting-without-resolver warning, looting/refill/buffs refusal errors).
- GUI: Start uses build_loop, simulation toggle exists, PerceptionWriter gone,
  test_ui smoke passes.
- Stray dirs gone, `customtkinter` in deps, CalibrationError friendly in CLI.
- Gates G2, G5, G6 from the findings doc are now satisfied by code (state how in your
  DONE message).
- Update `tests/instructions.md` baseline counts if the test set changed.

## DONE signal
Last line of your final message, exactly:
`T32b COMPLETE: <one-line summary>`
